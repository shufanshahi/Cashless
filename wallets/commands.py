"""Endpoint-facing entry points: combine a services.py operation with
idempotent response caching. These are what the API views (section 6) call.
"""

from . import services
from .idempotency import run_idempotent


def _serialize_transaction(txn):
    return {
        "id": str(txn.id),
        "wallet_id": str(txn.wallet_id),
        "type": txn.type,
        "amount": txn.amount,
        "balance_after": txn.balance_after,
        "idempotency_key": txn.idempotency_key,
        "created_at": txn.created_at.isoformat(),
    }


def deposit_command(*, tenant, wallet_id, amount, idempotency_key):
    request_data = {"wallet_id": str(wallet_id), "amount": amount}

    def _run():
        txn = services.deposit(
            tenant=tenant, wallet_id=wallet_id, amount=amount, idempotency_key=idempotency_key
        )
        return _serialize_transaction(txn), 201

    return run_idempotent(
        tenant=tenant, endpoint="deposit", key=idempotency_key, request_data=request_data, func=_run
    )


def withdraw_command(*, tenant, wallet_id, amount, idempotency_key):
    request_data = {"wallet_id": str(wallet_id), "amount": amount}

    def _run():
        txn = services.withdraw(
            tenant=tenant, wallet_id=wallet_id, amount=amount, idempotency_key=idempotency_key
        )
        return _serialize_transaction(txn), 201

    return run_idempotent(
        tenant=tenant, endpoint="withdraw", key=idempotency_key, request_data=request_data, func=_run
    )


def transfer_command(*, tenant, from_wallet_id, to_wallet_id, amount, idempotency_key):
    request_data = {
        "from_wallet_id": str(from_wallet_id),
        "to_wallet_id": str(to_wallet_id),
        "amount": amount,
    }

    def _run():
        out_txn, in_txn = services.transfer(
            tenant=tenant,
            from_wallet_id=from_wallet_id,
            to_wallet_id=to_wallet_id,
            amount=amount,
            idempotency_key=idempotency_key,
        )
        body = {
            "transfer_out": _serialize_transaction(out_txn),
            "transfer_in": _serialize_transaction(in_txn),
        }
        return body, 201

    return run_idempotent(
        tenant=tenant, endpoint="transfer", key=idempotency_key, request_data=request_data, func=_run
    )
