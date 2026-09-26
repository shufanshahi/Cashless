from rest_framework import serializers

from .models import Transaction, Wallet, WalletOwner


class WalletOwnerSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletOwner
        fields = ["id", "name", "external_ref", "created_at"]
        read_only_fields = ["id", "created_at"]


class WalletSerializer(serializers.ModelSerializer):
    owner = serializers.PrimaryKeyRelatedField(queryset=WalletOwner.objects.none())

    class Meta:
        model = Wallet
        fields = ["id", "owner", "currency", "balance", "created_at"]
        read_only_fields = ["id", "balance", "created_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Scope the owner choices to the requesting tenant so a client can
        # never attach a wallet to another tenant's owner, even by guessing
        # a valid UUID.
        request = self.context.get("request")
        if request is not None and getattr(request, "tenant", None) is not None:
            self.fields["owner"].queryset = WalletOwner.objects.filter(tenant=request.tenant)


class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = [
            "id",
            "wallet",
            "type",
            "amount",
            "balance_after",
            "idempotency_key",
            "related_transfer_id",
            "created_at",
        ]
        read_only_fields = fields


class DepositWithdrawSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=1)
    idempotency_key = serializers.CharField(max_length=255)


class TransferSerializer(serializers.Serializer):
    from_wallet = serializers.PrimaryKeyRelatedField(queryset=Wallet.objects.none())
    to_wallet = serializers.PrimaryKeyRelatedField(queryset=Wallet.objects.none())
    amount = serializers.IntegerField(min_value=1)
    idempotency_key = serializers.CharField(max_length=255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Scope both fields to the requesting tenant's wallets, same
        # reasoning as WalletSerializer.owner: a client should never be
        # able to even *reference* another tenant's wallet id.
        request = self.context.get("request")
        if request is not None and getattr(request, "tenant", None) is not None:
            qs = Wallet.objects.filter(tenant=request.tenant)
            self.fields["from_wallet"].queryset = qs
            self.fields["to_wallet"].queryset = qs

    def validate(self, attrs):
        if attrs["from_wallet"].id == attrs["to_wallet"].id:
            raise serializers.ValidationError("from_wallet and to_wallet must be different.")
        return attrs
