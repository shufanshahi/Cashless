"""Generic request/response idempotency, independent of any one endpoint.

Layered on top of the Transaction-level unique constraint from services.py:
that constraint stops a double-charge even if this layer were bypassed, but
on its own it would make a retried request *fail* (IntegrityError) rather
than transparently replay the original success. This module makes retries
genuinely no-ops from the client's point of view.
"""

import hashlib
import json

from django.db import transaction as db_transaction

from .exceptions import DomainError
from .models import IdempotencyKey


class IdempotencyConflictError(DomainError):
    default_code = "idempotency_key_conflict"


def hash_request(data):
    normalized = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def run_idempotent(*, tenant, endpoint, key, request_data, func):
    """Runs `func` at most once per (tenant, endpoint, key).

    `func` takes no arguments and returns (response_body, response_status),
    both JSON-serializable. Returns (response_body, response_status,
    was_replayed).

    - First call for a key: runs `func`, caches its result, returns it.
    - Retry with the same key AND the same request payload: skips `func`
      entirely and returns the cached result (was_replayed=True).
    - Retry with the same key but a DIFFERENT payload: raises
      IdempotencyConflictError rather than silently reusing or overwriting
      the original result.
    - If `func` raises: the whole atomic block (including the idempotency
      record itself) rolls back, so a failed attempt — e.g. insufficient
      funds — doesn't permanently consume the key. A later retry with a
      valid state can still succeed under the same key.
    """
    request_hash = hash_request(request_data)

    with db_transaction.atomic():
        # select_for_update() serializes concurrent callers on this exact
        # key: the second caller blocks here until the first's transaction
        # commits (or rolls back), so there's no window where two callers
        # both see "not created" and both run `func`.
        key_row, created = IdempotencyKey.objects.select_for_update().get_or_create(
            tenant=tenant,
            endpoint=endpoint,
            key=key,
            defaults={"request_hash": request_hash},
        )

        if not created:
            if key_row.request_hash != request_hash:
                raise IdempotencyConflictError(
                    f"idempotency_key '{key}' was already used for '{endpoint}' "
                    "with a different request body."
                )
            return key_row.response_body, key_row.response_status, True

        response_body, response_status = func()

        key_row.response_status = response_status
        key_row.response_body = response_body
        key_row.save(update_fields=["response_status", "response_body"])

    return response_body, response_status, False
