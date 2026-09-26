from rest_framework.test import APITestCase

from tenants.models import Tenant

from .models import Transaction, Wallet, WalletOwner


class WalletApiTestCase(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Acme")
        self.other_tenant = Tenant.objects.create(name="Other")

        self.owner = WalletOwner.objects.create(tenant=self.tenant, name="Alice")
        self.other_owner = WalletOwner.objects.create(tenant=self.other_tenant, name="Carol")

        self.wallet = Wallet.objects.create(tenant=self.tenant, owner=self.owner, balance=1000)
        self.other_wallet = Wallet.objects.create(
            tenant=self.other_tenant, owner=self.other_owner, balance=500
        )

    def auth(self, tenant=None):
        tenant = tenant or self.tenant
        return {"HTTP_AUTHORIZATION": f"Api-Key {tenant.api_key}"}


class DepositEndpointTests(WalletApiTestCase):
    def test_deposit_increases_balance(self):
        response = self.client.post(
            f"/api/wallets/{self.wallet.id}/deposit/",
            {"amount": 500, "idempotency_key": "dep-api-1"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["type"], "DEPOSIT")
        self.assertEqual(response.data["balance_after"], 1500)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1500)

    def test_deposit_retry_with_same_key_replays_response(self):
        payload = {"amount": 500, "idempotency_key": "dep-api-2"}
        first = self.client.post(f"/api/wallets/{self.wallet.id}/deposit/", payload, **self.auth())
        second = self.client.post(f"/api/wallets/{self.wallet.id}/deposit/", payload, **self.auth())

        self.assertEqual(first.data, second.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1500)
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet).count(), 1)

    def test_deposit_retry_with_different_amount_same_key_conflicts(self):
        self.client.post(
            f"/api/wallets/{self.wallet.id}/deposit/",
            {"amount": 500, "idempotency_key": "dep-api-3"},
            **self.auth(),
        )
        response = self.client.post(
            f"/api/wallets/{self.wallet.id}/deposit/",
            {"amount": 999, "idempotency_key": "dep-api-3"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"], "idempotency_key_conflict")

    def test_deposit_missing_idempotency_key_returns_400(self):
        response = self.client.post(
            f"/api/wallets/{self.wallet.id}/deposit/", {"amount": 500}, **self.auth()
        )
        self.assertEqual(response.status_code, 400)

    def test_deposit_zero_amount_returns_400(self):
        response = self.client.post(
            f"/api/wallets/{self.wallet.id}/deposit/",
            {"amount": 0, "idempotency_key": "dep-api-4"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_deposit_into_another_tenants_wallet(self):
        response = self.client.post(
            f"/api/wallets/{self.other_wallet.id}/deposit/",
            {"amount": 500, "idempotency_key": "dep-api-5"},
            **self.auth(self.tenant),
        )
        self.assertEqual(response.status_code, 404)


class WithdrawEndpointTests(WalletApiTestCase):
    def test_withdraw_decreases_balance(self):
        response = self.client.post(
            f"/api/wallets/{self.wallet.id}/withdraw/",
            {"amount": 300, "idempotency_key": "wd-api-1"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 700)

    def test_withdraw_insufficient_funds_returns_400_with_clear_error(self):
        response = self.client.post(
            f"/api/wallets/{self.wallet.id}/withdraw/",
            {"amount": 999999, "idempotency_key": "wd-api-2"},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "insufficient_funds")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1000)


class TransferEndpointTests(WalletApiTestCase):
    def setUp(self):
        super().setUp()
        self.owner_b = WalletOwner.objects.create(tenant=self.tenant, name="Bob")
        self.wallet_b = Wallet.objects.create(tenant=self.tenant, owner=self.owner_b, balance=0)

    def test_transfer_moves_funds_between_same_tenant_wallets(self):
        response = self.client.post(
            "/api/transfers/",
            {
                "from_wallet": str(self.wallet.id),
                "to_wallet": str(self.wallet_b.id),
                "amount": 400,
                "idempotency_key": "tr-api-1",
            },
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        self.wallet.refresh_from_db()
        self.wallet_b.refresh_from_db()
        self.assertEqual(self.wallet.balance, 600)
        self.assertEqual(self.wallet_b.balance, 400)

    def test_transfer_across_tenants_is_rejected(self):
        response = self.client.post(
            "/api/transfers/",
            {
                "from_wallet": str(self.wallet.id),
                "to_wallet": str(self.other_wallet.id),
                "amount": 100,
                "idempotency_key": "tr-api-2",
            },
            **self.auth(self.tenant),
        )
        # other_wallet isn't even a valid choice for this tenant's request,
        # so this is a 400 (serializer validation), not a 404/403 — either
        # way, the transfer must not happen.
        self.assertEqual(response.status_code, 400)
        self.wallet.refresh_from_db()
        self.other_wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1000)
        self.assertEqual(self.other_wallet.balance, 500)

    def test_transfer_to_same_wallet_returns_400(self):
        response = self.client.post(
            "/api/transfers/",
            {
                "from_wallet": str(self.wallet.id),
                "to_wallet": str(self.wallet.id),
                "amount": 100,
                "idempotency_key": "tr-api-3",
            },
            **self.auth(),
        )
        self.assertEqual(response.status_code, 400)


class TransactionHistoryEndpointTests(WalletApiTestCase):
    def test_transaction_history_is_paginated_and_scoped_to_wallet(self):
        for i in range(3):
            self.client.post(
                f"/api/wallets/{self.wallet.id}/deposit/",
                {"amount": 100, "idempotency_key": f"hist-{i}"},
                **self.auth(),
            )

        response = self.client.get(f"/api/wallets/{self.wallet.id}/transactions/", **self.auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)
        self.assertIn("results", response.data)

    def test_cannot_read_another_tenants_wallet_history(self):
        response = self.client.get(
            f"/api/wallets/{self.other_wallet.id}/transactions/", **self.auth(self.tenant)
        )
        self.assertEqual(response.status_code, 404)
