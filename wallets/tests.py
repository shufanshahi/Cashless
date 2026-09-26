import threading
import uuid

from django.db import connection
from django.test import TestCase, TransactionTestCase

from tenants.models import Tenant

from . import commands, services
from .exceptions import (
    InsufficientFundsError,
    InvalidAmountError,
    SameWalletTransferError,
    WalletNotFoundError,
)
from .idempotency import IdempotencyConflictError
from .models import IdempotencyKey, Transaction, Wallet, WalletOwner


class DepositTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Acme")
        self.owner = WalletOwner.objects.create(tenant=self.tenant, name="Alice")
        self.wallet = Wallet.objects.create(tenant=self.tenant, owner=self.owner, balance=0)

    def test_deposit_increases_balance_and_creates_ledger_row(self):
        txn = services.deposit(
            tenant=self.tenant, wallet_id=self.wallet.id, amount=1000, idempotency_key="dep-1"
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1000)
        self.assertEqual(txn.type, Transaction.Type.DEPOSIT)
        self.assertEqual(txn.balance_after, 1000)
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet).count(), 1)

    def test_deposit_rejects_non_positive_amount(self):
        with self.assertRaises(InvalidAmountError):
            services.deposit(tenant=self.tenant, wallet_id=self.wallet.id, amount=0, idempotency_key="dep-2")

    def test_deposit_into_nonexistent_wallet_raises(self):
        with self.assertRaises(WalletNotFoundError):
            services.deposit(
                tenant=self.tenant, wallet_id=uuid.uuid4(), amount=100, idempotency_key="dep-3"
            )


class WithdrawTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Acme")
        self.owner = WalletOwner.objects.create(tenant=self.tenant, name="Alice")
        self.wallet = Wallet.objects.create(tenant=self.tenant, owner=self.owner, balance=1000)

    def test_withdraw_decreases_balance_and_creates_ledger_row(self):
        txn = services.withdraw(
            tenant=self.tenant, wallet_id=self.wallet.id, amount=400, idempotency_key="wd-1"
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 600)
        self.assertEqual(txn.type, Transaction.Type.WITHDRAWAL)
        self.assertEqual(txn.balance_after, 600)

    def test_withdraw_with_insufficient_funds_raises_and_changes_nothing(self):
        with self.assertRaises(InsufficientFundsError):
            services.withdraw(
                tenant=self.tenant, wallet_id=self.wallet.id, amount=5000, idempotency_key="wd-2"
            )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1000)
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet).count(), 0)

    def test_withdraw_from_nonexistent_wallet_raises(self):
        with self.assertRaises(WalletNotFoundError):
            services.withdraw(
                tenant=self.tenant, wallet_id=uuid.uuid4(), amount=100, idempotency_key="wd-3"
            )


class TransferTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Acme")
        self.other_tenant = Tenant.objects.create(name="Other")
        self.owner_a = WalletOwner.objects.create(tenant=self.tenant, name="Alice")
        self.owner_b = WalletOwner.objects.create(tenant=self.tenant, name="Bob")
        self.owner_c = WalletOwner.objects.create(tenant=self.other_tenant, name="Carol")
        self.wallet_a = Wallet.objects.create(tenant=self.tenant, owner=self.owner_a, balance=1000)
        self.wallet_b = Wallet.objects.create(tenant=self.tenant, owner=self.owner_b, balance=0)
        self.wallet_c = Wallet.objects.create(tenant=self.other_tenant, owner=self.owner_c, balance=0)

    def test_transfer_moves_funds_atomically_with_two_ledger_legs(self):
        out_txn, in_txn = services.transfer(
            tenant=self.tenant,
            from_wallet_id=self.wallet_a.id,
            to_wallet_id=self.wallet_b.id,
            amount=300,
            idempotency_key="tr-1",
        )
        self.wallet_a.refresh_from_db()
        self.wallet_b.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, 700)
        self.assertEqual(self.wallet_b.balance, 300)
        self.assertEqual(out_txn.type, Transaction.Type.TRANSFER_OUT)
        self.assertEqual(in_txn.type, Transaction.Type.TRANSFER_IN)
        self.assertEqual(out_txn.related_transfer_id, in_txn.related_transfer_id)

    def test_transfer_with_insufficient_funds_changes_nothing(self):
        with self.assertRaises(InsufficientFundsError):
            services.transfer(
                tenant=self.tenant,
                from_wallet_id=self.wallet_a.id,
                to_wallet_id=self.wallet_b.id,
                amount=5000,
                idempotency_key="tr-2",
            )
        self.wallet_a.refresh_from_db()
        self.wallet_b.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, 1000)
        self.assertEqual(self.wallet_b.balance, 0)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_transfer_to_same_wallet_is_rejected(self):
        with self.assertRaises(SameWalletTransferError):
            services.transfer(
                tenant=self.tenant,
                from_wallet_id=self.wallet_a.id,
                to_wallet_id=self.wallet_a.id,
                amount=100,
                idempotency_key="tr-3",
            )

    def test_cross_tenant_transfer_is_rejected(self):
        with self.assertRaises(WalletNotFoundError):
            services.transfer(
                tenant=self.tenant,
                from_wallet_id=self.wallet_a.id,
                to_wallet_id=self.wallet_c.id,
                amount=100,
                idempotency_key="tr-4",
            )
        self.wallet_a.refresh_from_db()
        self.wallet_c.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, 1000)
        self.assertEqual(self.wallet_c.balance, 0)


class ConcurrencyTests(TransactionTestCase):
    """TransactionTestCase (not TestCase) on purpose: TestCase wraps each
    test in one outer transaction on the main thread's connection, so a
    second thread's select_for_update would block forever waiting on a lock
    that's never actually committed/released. TransactionTestCase commits
    for real, so concurrent threads with their own DB connections behave
    the way they would in production.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Acme")
        self.owner = WalletOwner.objects.create(tenant=self.tenant, name="Alice")
        self.wallet = Wallet.objects.create(tenant=self.tenant, owner=self.owner, balance=1000)

    def test_concurrent_withdrawals_do_not_overdraw(self):
        # Two concurrent withdrawals of 800 against a balance of 1000: only
        # one can succeed. If select_for_update weren't working, both could
        # read balance=1000 before either writes, and both would "succeed",
        # leaving the wallet at -600.
        results = []

        def do_withdraw(key):
            try:
                services.withdraw(
                    tenant=self.tenant, wallet_id=self.wallet.id, amount=800, idempotency_key=key
                )
                results.append("ok")
            except InsufficientFundsError:
                results.append("insufficient")
            finally:
                connection.close()

        threads = [threading.Thread(target=do_withdraw, args=(f"cw-{i}",)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.wallet.refresh_from_db()
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count("insufficient"), 1)
        self.assertEqual(self.wallet.balance, 200)
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet).count(), 1)

    def test_concurrent_opposite_direction_transfers_do_not_deadlock(self):
        # Two threads repeatedly transfer the same pair of wallets in
        # opposite directions (A->B and B->A) at the same time. If the two
        # legs of a transfer locked their wallets in request order instead
        # of a consistent id order, this is exactly the pattern that
        # deadlocks: thread 1 holds A waiting for B while thread 2 holds B
        # waiting for A. Postgres's default deadlock_timeout is ~1s, so a
        # real deadlock would surface here as an OperationalError and/or a
        # thread that never finishes.
        wallet_a = self.wallet
        owner_b = WalletOwner.objects.create(tenant=self.tenant, name="Bob")
        wallet_b = Wallet.objects.create(tenant=self.tenant, owner=owner_b, balance=1000)

        iterations = 20
        errors = []

        def transfer_loop(from_wallet, to_wallet, key_prefix):
            for i in range(iterations):
                try:
                    services.transfer(
                        tenant=self.tenant,
                        from_wallet_id=from_wallet.id,
                        to_wallet_id=to_wallet.id,
                        amount=10,
                        idempotency_key=f"{key_prefix}-{i}",
                    )
                except Exception as exc:  # noqa: BLE001 - capturing any DB-level error, not just ours
                    errors.append(exc)
                finally:
                    connection.close()

        t1 = threading.Thread(target=transfer_loop, args=(wallet_a, wallet_b, "a-to-b"))
        t2 = threading.Thread(target=transfer_loop, args=(wallet_b, wallet_a, "b-to-a"))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertFalse(t1.is_alive(), "A->B transfer thread appears to have deadlocked")
        self.assertFalse(t2.is_alive(), "B->A transfer thread appears to have deadlocked")
        self.assertEqual(errors, [])

        wallet_a.refresh_from_db()
        wallet_b.refresh_from_db()
        # Equal numbers of 10-unit transfers each way net to zero change.
        self.assertEqual(wallet_a.balance, 1000)
        self.assertEqual(wallet_b.balance, 1000)
        self.assertEqual(
            Transaction.objects.filter(tenant=self.tenant, type=Transaction.Type.TRANSFER_OUT).count(),
            iterations * 2,
        )

    def test_concurrent_duplicate_idempotency_key_deposits_only_apply_once(self):
        # Five threads fire the *same* idempotency_key concurrently. The
        # unique constraint on (tenant, idempotency_key, type) must let
        # exactly one through even under a real race, not just sequentially.
        results = []

        def do_deposit():
            try:
                services.deposit(
                    tenant=self.tenant, wallet_id=self.wallet.id, amount=500, idempotency_key="dup-key"
                )
                results.append("ok")
            except Exception:
                results.append("blocked")
            finally:
                connection.close()

        threads = [threading.Thread(target=do_deposit) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.wallet.refresh_from_db()
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(self.wallet.balance, 1500)  # starting balance 1000 + exactly one +500
        self.assertEqual(
            Transaction.objects.filter(wallet=self.wallet, idempotency_key="dup-key").count(), 1
        )

    def test_concurrent_identical_deposit_commands_replay_the_same_response(self):
        # Five threads fire the exact same deposit_command call (same
        # wallet, amount, idempotency_key). Exactly one should actually run
        # the deposit; the rest must replay its cached response verbatim,
        # not attempt a second deposit and fail.
        responses = []
        lock = threading.Lock()

        def do_deposit():
            body, status, replayed = commands.deposit_command(
                tenant=self.tenant, wallet_id=self.wallet.id, amount=250, idempotency_key="concurrent-dep"
            )
            with lock:
                responses.append((body, status, replayed))
            connection.close()

        threads = [threading.Thread(target=do_deposit) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.wallet.refresh_from_db()
        self.assertEqual(len(responses), 5)
        self.assertEqual(self.wallet.balance, 1250)
        self.assertEqual(
            Transaction.objects.filter(wallet=self.wallet, idempotency_key="concurrent-dep").count(), 1
        )
        # Every thread must have gotten back the identical response body.
        first_body = responses[0][0]
        for body, status, _replayed in responses:
            self.assertEqual(body, first_body)
            self.assertEqual(status, 201)
        # Exactly one of the five actually executed the deposit.
        self.assertEqual(sum(1 for _, _, replayed in responses if not replayed), 1)
        self.assertEqual(sum(1 for _, _, replayed in responses if replayed), 4)


class IdempotencyCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Acme")
        self.owner = WalletOwner.objects.create(tenant=self.tenant, name="Alice")
        self.wallet = Wallet.objects.create(tenant=self.tenant, owner=self.owner, balance=1000)

    def test_retry_with_identical_payload_replays_cached_response(self):
        body1, status1, replayed1 = commands.deposit_command(
            tenant=self.tenant, wallet_id=self.wallet.id, amount=300, idempotency_key="idem-1"
        )
        body2, status2, replayed2 = commands.deposit_command(
            tenant=self.tenant, wallet_id=self.wallet.id, amount=300, idempotency_key="idem-1"
        )

        self.assertFalse(replayed1)
        self.assertTrue(replayed2)
        self.assertEqual(body1, body2)
        self.assertEqual(status1, status2)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1300)
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet).count(), 1)

    def test_retry_with_different_payload_raises_conflict(self):
        commands.deposit_command(
            tenant=self.tenant, wallet_id=self.wallet.id, amount=300, idempotency_key="idem-2"
        )
        with self.assertRaises(IdempotencyConflictError):
            commands.deposit_command(
                tenant=self.tenant, wallet_id=self.wallet.id, amount=999, idempotency_key="idem-2"
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1300)  # only the first deposit applied
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet).count(), 1)

    def test_failed_attempt_does_not_permanently_consume_the_key(self):
        with self.assertRaises(InsufficientFundsError):
            commands.withdraw_command(
                tenant=self.tenant, wallet_id=self.wallet.id, amount=999999, idempotency_key="idem-3"
            )

        # The failed attempt must leave no trace: not the ledger, not the
        # idempotency record. A retry with the same key should be free to
        # succeed once the underlying condition is fixed.
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet).count(), 0)
        self.assertEqual(IdempotencyKey.objects.filter(key="idem-3").count(), 0)

        body, status, replayed = commands.withdraw_command(
            tenant=self.tenant, wallet_id=self.wallet.id, amount=400, idempotency_key="idem-3"
        )
        self.assertFalse(replayed)
        self.assertEqual(status, 201)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 600)

    def test_transfer_command_is_idempotent_across_both_legs(self):
        owner_b = WalletOwner.objects.create(tenant=self.tenant, name="Bob")
        wallet_b = Wallet.objects.create(tenant=self.tenant, owner=owner_b, balance=0)

        body1, _, replayed1 = commands.transfer_command(
            tenant=self.tenant,
            from_wallet_id=self.wallet.id,
            to_wallet_id=wallet_b.id,
            amount=200,
            idempotency_key="idem-transfer",
        )
        body2, _, replayed2 = commands.transfer_command(
            tenant=self.tenant,
            from_wallet_id=self.wallet.id,
            to_wallet_id=wallet_b.id,
            amount=200,
            idempotency_key="idem-transfer",
        )

        self.assertFalse(replayed1)
        self.assertTrue(replayed2)
        self.assertEqual(body1, body2)

        self.wallet.refresh_from_db()
        wallet_b.refresh_from_db()
        self.assertEqual(self.wallet.balance, 800)
        self.assertEqual(wallet_b.balance, 200)
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet).count(), 1)
        self.assertEqual(Transaction.objects.filter(wallet=wallet_b).count(), 1)
