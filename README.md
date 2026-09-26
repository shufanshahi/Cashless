# Multi-Tenant Wallet API

A small wallet / ledger service where multiple tenants (merchants or
organizations) share the same platform, but each tenant's data — wallets,
owners, transactions — is fully isolated from every other tenant's.

Built with Django + Django REST Framework, backed by PostgreSQL.

## Setup

### Option A: local (Python + a local Postgres)

```bash
git clone <this-repo>
cd Cashless
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a Postgres database and role (adjust to taste):

```bash
sudo -u postgres psql -c "CREATE USER cashless WITH PASSWORD 'cashless';"
sudo -u postgres psql -c "ALTER USER cashless CREATEDB;"   # needed to run tests
sudo -u postgres psql -c "CREATE DATABASE cashless_wallet OWNER cashless;"
```

Copy `.env.example` to `.env` and fill in the password:

```bash
cp .env.example .env
```

```
DEBUG=True
SECRET_KEY=dev-secret-key-change-me
DB_NAME=cashless_wallet
DB_USER=cashless
DB_PASSWORD=cashless
DB_HOST=localhost
DB_PORT=5432
```

Then:

```bash
python manage.py migrate
python manage.py runserver
```

The API is now at `http://localhost:8000/api/`.

### Option B: Docker Compose

```bash
cp .env.example .env   # values are read by both services
docker compose build
docker compose up -d
docker compose exec web python manage.py migrate
```

The `db` service maps to **host port 5433** (not 5432), so it won't collide
with a Postgres you might already have running locally. Inside the compose
network, the `web` service talks to the database at `db:5432` regardless of
what `DB_HOST` says in `.env` — see the comment in `docker-compose.yml`.

### Seeding demo data

Either way, once migrated you can seed a demo tenant with two wallets
instead of creating everything by hand:

```bash
python manage.py seed_demo
# (or: docker compose exec web python manage.py seed_demo)
```

It prints the tenant's API key, both wallet ids, and a ready-to-run `curl`
example. Safe to re-run — it reuses existing records instead of duplicating
them.

## Authentication

Every request (except tenant creation) must identify its tenant, via one of:

- **`Authorization: Api-Key <key>`** — the real credential. The key is
  generated when a tenant is created and returned exactly once.
- **`X-Tenant-ID: <uuid>`** — convenience for local testing. **This is not a
  security boundary on its own** — tenant ids aren't secret — so treat it as
  a dev/test shortcut, not something to expose in a real deployment.

Creating a tenant is the one unauthenticated endpoint (there's no other way
to get your first API key).

```bash
curl -X POST http://localhost:8000/api/tenants/ -d "name=Acme Corp"
# => {"id": "...", "name": "Acme Corp", "api_key": "...", "created_at": "..."}
```

## API Reference

All bodies are `application/x-www-form-urlencoded` or JSON; examples below
use JSON. `<KEY>` is the tenant's API key from above.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/tenants/` | Create a tenant (unauthenticated) |
| `POST` | `/api/wallet-owners/` | Create a user under the tenant |
| `GET` | `/api/wallet-owners/` | List the tenant's users |
| `POST` | `/api/wallets/` | Create a wallet for one of the tenant's owners |
| `GET` | `/api/wallets/` | List the tenant's wallets (paginated) |
| `GET` | `/api/wallets/{id}/` | Wallet detail, including current balance |
| `GET` | `/api/wallets/{id}/transactions/` | Paginated ledger history for the wallet |
| `POST` | `/api/wallets/{id}/deposit/` | Deposit funds (idempotent) |
| `POST` | `/api/wallets/{id}/withdraw/` | Withdraw funds (idempotent, rejects insufficient balance) |
| `POST` | `/api/transfers/` | Transfer between two of the tenant's own wallets (idempotent) |

### Create a wallet owner

```bash
curl -X POST http://localhost:8000/api/wallet-owners/ \
  -H "Authorization: Api-Key <KEY>" \
  -d "name=Alice"
# => {"id": "...", "name": "Alice", "external_ref": null, "created_at": "..."}
```

### Create a wallet

```bash
curl -X POST http://localhost:8000/api/wallets/ \
  -H "Authorization: Api-Key <KEY>" \
  -d "owner=<owner-id>"
# => {"id": "...", "owner": "...", "currency": "INR", "balance": 0, "created_at": "..."}
```

### Deposit

```bash
curl -X POST http://localhost:8000/api/wallets/<wallet-id>/deposit/ \
  -H "Authorization: Api-Key <KEY>" -H "Content-Type: application/json" \
  -d '{"amount": 1000, "idempotency_key": "client-generated-uuid-1"}'
# => {"id": "...", "wallet_id": "...", "type": "DEPOSIT", "amount": 1000,
#     "balance_after": 1000, "idempotency_key": "...", "created_at": "..."}
```

### Withdraw

Same shape as deposit, at `/withdraw/`. Returns `400` with
`{"error": "insufficient_funds", "detail": "..."}` if the balance is too low.

### Transfer

```bash
curl -X POST http://localhost:8000/api/transfers/ \
  -H "Authorization: Api-Key <KEY>" -H "Content-Type: application/json" \
  -d '{"from_wallet": "<id>", "to_wallet": "<id>", "amount": 500, "idempotency_key": "client-generated-uuid-2"}'
# => {"transfer_out": {...}, "transfer_in": {...}}
```

`from_wallet`/`to_wallet` must both belong to the requesting tenant —
neither field will even validate against another tenant's wallet id.

### Error shape

Business-rule failures return a consistent body:

```json
{ "error": "insufficient_funds", "detail": "Wallet balance 500 is less than requested 1000." }
```

| `error` code | HTTP status | Meaning |
|---|---|---|
| `insufficient_funds` | 400 | Withdraw/transfer would overdraw the source wallet |
| `invalid_amount` | 400 | Amount isn't a positive integer |
| `same_wallet_transfer` | 400 | `from_wallet` and `to_wallet` are the same |
| `wallet_not_found` | 404 | Wallet doesn't exist for this tenant — also what a cross-tenant wallet id looks like |
| `idempotency_key_conflict` | 409 | Same `idempotency_key` reused with a different request body |

Field-level validation errors (missing/malformed input) use DRF's standard
`{"field_name": ["message"]}` shape instead, since those are a different
kind of error (client sent a malformed request, not a business rule
violation).

## Design Notes

**Tenant isolation** is enforced in three overlapping layers, not just one
check, so a bug in any single layer doesn't expose data:
1. Every tenant-owned model (`Wallet`, `WalletOwner`, `Transaction`,
   `IdempotencyKey`) carries a direct `tenant` foreign key, denormalized
   rather than reached through a join — every query filters on it directly.
2. `TenantScopedModelViewSet` (in `tenants/mixins.py`) makes this automatic:
   `get_queryset()` always filters by the authenticated tenant,
   `perform_create()` always stamps the tenant from the request (a client
   can never set it), and `get_object()` re-checks the tenant and raises
   `404` — not `403` — if it doesn't match, so a wrong id never confirms
   another tenant's object exists.
3. For transfers, both wallet ids come from the request body rather than
   the URL, so isolation is enforced at the serializer level too: the
   `from_wallet`/`to_wallet` `PrimaryKeyRelatedField`s are scoped to the
   requesting tenant's wallets in `__init__`, meaning another tenant's
   wallet id is never even a *valid choice* — it fails serializer
   validation (400) before the service layer is reached.

**Ledger as source of truth.** `Wallet.balance` is a cached integer
(minor units — paisa/cents), but it is only ever written inside the same
database transaction that inserts the corresponding `Transaction` row, so
it can never drift from the ledger. `Transaction` rows are immutable — the
model's `save()` raises if called on an existing row — and admin has
change/delete permission disabled for the same reason. If a bug were ever
suspected, the ledger could be resummed and diffed against `balance` to
verify consistency; a real production system might do this as a periodic
reconciliation job, but it seemed out of scope for the time-box here.

**Row locking.** `deposit`, `withdraw`, and `transfer` (in `wallets/services.py`)
all take `select_for_update()` on the wallet row(s) inside
`transaction.atomic()` before reading the balance, so concurrent operations
on the same wallet serialize instead of racing. `transfer` locks **both**
wallets in a single query, `.order_by("id")`, regardless of which wallet is
`from_wallet` and which is `to_wallet` — this is what prevents deadlock
when two transfers move money between the same pair of wallets in opposite
directions at the same time; without a consistent lock order, thread 1
could hold wallet A waiting for B while thread 2 holds B waiting for A.
This is directly tested (`test_concurrent_opposite_direction_transfers_do_not_deadlock`
in `wallets/tests.py`).

**Idempotency** works in two layers:
1. A DB-level unique constraint on `Transaction(tenant, idempotency_key, type)`
   is the hard backstop — it physically cannot let a duplicate deposit or
   withdrawal (or either leg of a transfer) commit twice, even under a raw
   race with no other protection.
2. `wallets/idempotency.py`'s `run_idempotent()` is what makes retries
   pleasant rather than just safe: it locks (or creates) an `IdempotencyKey`
   row for `(tenant, endpoint, key)` inside the same transaction as the
   actual operation, and on a retry with an identical payload, replays the
   **exact cached response** instead of re-running any logic. A retry with
   the *same key but a different payload* gets `409 Conflict` rather than
   silently doing something unexpected. Critically, if the wrapped
   operation fails (e.g. insufficient funds), the whole transaction —
   including the idempotency record itself — rolls back, so a failed
   attempt doesn't permanently "use up" the key; a later retry once the
   underlying condition is fixed can still succeed.

**Why UUID primary keys.** Sequential integer ids would let one tenant
guess how many tenants/wallets exist elsewhere on the platform, or guess
another tenant's object ids to probe with. UUIDs avoid that for free.
<!-- 
## Assumptions

- **Single currency semantics.** `Wallet.currency` exists and defaults to
  `"INR"`, but nothing in the service layer converts between currencies —
  a transfer just moves the integer amount as-is. Cross-currency transfers
  aren't validated against or rejected; this felt out of scope for the
  time-box.
- **No end-user authentication.** A "user" in this system is a
  `WalletOwner` record — a label a wallet is attached to — not a login
  account. The assessment's grading criteria are about money handling and
  tenancy, not an auth/session system, so this was kept minimal
  deliberately.
- **Wallet/owner creation are separate endpoints**, not a single combined
  "create user + wallet" call. This matches how the two concerns are
  actually independent (an owner could have zero or multiple wallets) and
  keeps each endpoint single-purpose.
- **`X-Tenant-ID` header is accepted as an alternative to the API key**,
  purely for convenience when testing by hand. It is explicitly documented
  above as not being a real security boundary.
- **Tenant creation is unauthenticated** and returns the `api_key` in the
  response body exactly once. A real system would show it once and store
  only a hash server-side; this was simplified given the time-box.
- **Cross-tenant transfer targets surface as `wallet_not_found` (404),
  not a distinct "cross-tenant" error** — consistent with the isolation
  principle of never confirming another tenant's object exists.
- **Amounts are always positive integers in minor units** (e.g. paisa/cents).
  There's no currency-formatting or decimal handling in the API layer; the
  client is expected to send/interpret minor units directly.

## Running Tests

```bash
pytest                        # full suite
pytest --cov=wallets --cov=tenants --cov-report=term-missing   # with coverage
```

Tests run against a real PostgreSQL test database (created/destroyed
automatically by `pytest-django`) rather than SQLite — this matters because
several tests exercise real row locking and deadlock behavior
(`select_for_update`, concurrent transfers) that SQLite doesn't model the
same way. The Postgres role used for the app also needs `CREATEDB` for this
to work locally:

```bash
sudo -u postgres psql -c "ALTER USER cashless CREATEDB;"
```

**50 tests, 96% coverage overall, 100% on the business-logic modules**
(`services.py`, `idempotency.py`, `commands.py`, `serializers.py`,
`views.py`). Coverage includes, per the assessment's explicit ask:

- Insufficient funds on withdraw and transfer (rejected, nothing committed)
- Concurrent transfers/withdrawals via real `threading` + `TransactionTestCase`
  against Postgres — both overdraw-prevention and deadlock-avoidance
- Duplicate idempotency key, both sequential and under a genuine
  concurrent race (5 threads firing the identical request)
- Cross-tenant access blocked at every layer: list, retrieve, create,
  deposit, withdraw, transfer, and transaction history

## What's Out of Scope / Trade-offs

Given the ~4–5 hour time-box, these were deliberately left out:

- Rate limiting / throttling
- A background reconciliation job to verify `balance` against the summed
  ledger (the invariant is enforced structurally instead — see Design Notes)
- Multi-currency conversion
- Soft-delete / audit trail beyond the ledger itself (the ledger *is* the
  audit trail for money movement; nothing else in the system needs one)
- API versioning, OpenAPI/Swagger schema generation
- Production-grade deployment config (gunicorn/nginx, secrets management,
  HTTPS termination) — Docker Compose here is for local dev convenience only -->
