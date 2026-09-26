from django.core.management.base import BaseCommand

from tenants.models import Tenant
from wallets.models import Wallet, WalletOwner


class Command(BaseCommand):
    help = "Seeds a demo tenant with two wallets, for manual curl/Postman testing. Safe to re-run."

    def handle(self, *args, **options):
        tenant, created = Tenant.objects.get_or_create(name="Demo Tenant")
        self.stdout.write(
            self.style.SUCCESS(f"{'Created' if created else 'Reusing'} tenant: {tenant.name}")
        )

        alice, _ = WalletOwner.objects.get_or_create(
            tenant=tenant, external_ref="alice@demo.test", defaults={"name": "Alice"}
        )
        bob, _ = WalletOwner.objects.get_or_create(
            tenant=tenant, external_ref="bob@demo.test", defaults={"name": "Bob"}
        )

        alice_wallet, _ = Wallet.objects.get_or_create(
            tenant=tenant, owner=alice, defaults={"balance": 10000}
        )
        bob_wallet, _ = Wallet.objects.get_or_create(tenant=tenant, owner=bob, defaults={"balance": 0})

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Demo data ready:"))
        self.stdout.write(f"  Tenant ID:    {tenant.id}")
        self.stdout.write(f"  API Key:      {tenant.api_key}")
        self.stdout.write(f"  Alice wallet: {alice_wallet.id} (balance={alice_wallet.balance})")
        self.stdout.write(f"  Bob wallet:   {bob_wallet.id} (balance={bob_wallet.balance})")
        self.stdout.write("")
        self.stdout.write("Example curl (deposit into Alice's wallet):")
        self.stdout.write(
            f"  curl -X POST http://localhost:8000/api/wallets/{alice_wallet.id}/deposit/ \\\n"
            f'    -H "Authorization: Api-Key {tenant.api_key}" \\\n'
            f'    -H "Content-Type: application/json" \\\n'
            f"    -d '{{\"amount\": 500, \"idempotency_key\": \"demo-1\"}}'"
        )
