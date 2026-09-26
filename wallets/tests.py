import threading

from django.db import connection
from django.test import TestCase, TransactionTestCase

from tenants.models import Tenant

from . import services
from .exceptions import (
    InsufficientFundsError,
    InvalidAmountError,
    SameWalletTransferError,
    WalletNotFoundError,
)
from .models import Transaction, Wallet, WalletOwner


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
