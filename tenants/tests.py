from rest_framework.test import APITestCase

from wallets.models import Wallet, WalletOwner

from .models import Tenant


class TenantCreationTests(APITestCase):
    def test_create_tenant_is_unauthenticated_and_returns_api_key(self):
        response = self.client.post("/api/tenants/", {"name": "Acme Corp"})
        self.assertEqual(response.status_code, 201)
        self.assertIn("api_key", response.data)
        self.assertEqual(len(response.data["api_key"]), 64)

    def test_create_tenant_without_name_returns_400(self):
        response = self.client.post("/api/tenants/", {})
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data)


class TenantAuthenticationTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Acme Corp")

    def test_missing_credentials_returns_401(self):
        response = self.client.get("/api/wallets/")
        self.assertEqual(response.status_code, 401)

    def test_invalid_api_key_returns_401(self):
        response = self.client.get("/api/wallets/", HTTP_AUTHORIZATION="Api-Key wrong-key")
        self.assertEqual(response.status_code, 401)

    def test_valid_api_key_authenticates(self):
        response = self.client.get(
            "/api/wallets/", HTTP_AUTHORIZATION=f"Api-Key {self.tenant.api_key}"
        )
        self.assertEqual(response.status_code, 200)

    def test_valid_tenant_id_header_authenticates(self):
        response = self.client.get("/api/wallets/", HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(response.status_code, 200)

    def test_invalid_tenant_id_header_returns_401(self):
        response = self.client.get("/api/wallets/", HTTP_X_TENANT_ID="not-a-uuid")
        self.assertEqual(response.status_code, 401)


class TenantIsolationTests(APITestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Tenant A")
        self.tenant_b = Tenant.objects.create(name="Tenant B")

        self.owner_a = WalletOwner.objects.create(tenant=self.tenant_a, name="Alice")
        self.owner_b = WalletOwner.objects.create(tenant=self.tenant_b, name="Bob")

        self.wallet_a = Wallet.objects.create(tenant=self.tenant_a, owner=self.owner_a, balance=1000)
        self.wallet_b = Wallet.objects.create(tenant=self.tenant_b, owner=self.owner_b, balance=500)

    def auth(self, tenant):
        return {"HTTP_AUTHORIZATION": f"Api-Key {tenant.api_key}"}

    def test_tenant_only_sees_own_wallets_in_list(self):
        response = self.client.get("/api/wallets/", **self.auth(self.tenant_a))
        ids = [w["id"] for w in response.data["results"]]
        self.assertIn(str(self.wallet_a.id), ids)
        self.assertNotIn(str(self.wallet_b.id), ids)

    def test_tenant_cannot_retrieve_other_tenants_wallet(self):
        response = self.client.get(f"/api/wallets/{self.wallet_b.id}/", **self.auth(self.tenant_a))
        self.assertEqual(response.status_code, 404)

    def test_tenant_cannot_create_wallet_for_other_tenants_owner(self):
        response = self.client.post(
            "/api/wallets/",
            {"owner": str(self.owner_b.id), "currency": "INR"},
            **self.auth(self.tenant_a),
        )
        self.assertEqual(response.status_code, 400)

    def test_created_wallet_is_stamped_with_requesting_tenant(self):
        response = self.client.post(
            "/api/wallets/",
            {"owner": str(self.owner_a.id), "currency": "INR"},
            **self.auth(self.tenant_a),
        )
        self.assertEqual(response.status_code, 201)
        wallet = Wallet.objects.get(id=response.data["id"])
        self.assertEqual(wallet.tenant_id, self.tenant_a.id)
