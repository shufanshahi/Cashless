import uuid

from django.db import models

from tenants.models import Tenant


class WalletOwner(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="wallet_owners")
    name = models.CharField(max_length=255)
    external_ref = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "external_ref"],
                name="unique_owner_external_ref_per_tenant",
                condition=models.Q(external_ref__isnull=False),
            )
        ]

    def __str__(self):
        return self.name


class Wallet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="wallets")
    owner = models.ForeignKey(WalletOwner, on_delete=models.PROTECT, related_name="wallets")
    currency = models.CharField(max_length=3, default="INR")
    balance = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(balance__gte=0), name="wallet_balance_non_negative"),
        ]

    def __str__(self):
        return f"{self.owner.name} ({self.currency})"


class Transaction(models.Model):
    class Type(models.TextChoices):
        DEPOSIT = "DEPOSIT", "Deposit"
        WITHDRAWAL = "WITHDRAWAL", "Withdrawal"
        TRANSFER_IN = "TRANSFER_IN", "Transfer In"
        TRANSFER_OUT = "TRANSFER_OUT", "Transfer Out"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="transactions")
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="transactions")
    type = models.CharField(max_length=20, choices=Type.choices)
    amount = models.PositiveBigIntegerField()
    balance_after = models.BigIntegerField()
    idempotency_key = models.CharField(max_length=255)
    related_transfer_id = models.UUIDField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Scoped by (tenant, idempotency_key, type) rather than just (tenant, idempotency_key):
            # a transfer writes two legs (TRANSFER_OUT + TRANSFER_IN) under the *same* idempotency_key,
            # so the key alone can't be unique — but a duplicate DEPOSIT/WITHDRAWAL/leg with the same
            # key+type is exactly the double-charge this constraint exists to block.
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key", "type"],
                name="unique_idempotency_key_per_tenant_and_type",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "wallet", "-created_at"]),
            models.Index(fields=["tenant", "idempotency_key"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk and Transaction.objects.filter(pk=self.pk).exists():
            raise ValueError("Transaction records are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.type} {self.amount} on {self.wallet_id}"


class IdempotencyKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="idempotency_keys")
    endpoint = models.CharField(max_length=50)
    key = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "endpoint", "key"],
                name="unique_idempotency_key_per_tenant_and_endpoint",
            ),
        ]

    def __str__(self):
        return f"{self.endpoint}:{self.key}"
