from rest_framework import serializers

from .models import Wallet, WalletOwner


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
