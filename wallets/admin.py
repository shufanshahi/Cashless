from django.contrib import admin

from .models import IdempotencyKey, Transaction, Wallet, WalletOwner


@admin.register(WalletOwner)
class WalletOwnerAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "name", "external_ref", "created_at")
    list_filter = ("tenant",)


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "owner", "currency", "balance", "created_at")
    list_filter = ("tenant", "currency")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "wallet", "type", "amount", "balance_after", "idempotency_key", "created_at")
    list_filter = ("tenant", "type")
    readonly_fields = [f.name for f in Transaction._meta.fields]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "endpoint", "key", "response_status", "created_at")
    list_filter = ("tenant", "endpoint")
