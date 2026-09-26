from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from tenants.mixins import TenantScopedModelViewSet

from . import commands
from .models import Transaction, Wallet, WalletOwner
from .serializers import (
    DepositWithdrawSerializer,
    TransactionSerializer,
    TransferSerializer,
    WalletOwnerSerializer,
    WalletSerializer,
)


class WalletOwnerViewSet(TenantScopedModelViewSet, viewsets.ModelViewSet):
    queryset = WalletOwner.objects.all()
    serializer_class = WalletOwnerSerializer
    http_method_names = ["get", "post", "head", "options"]


class WalletViewSet(TenantScopedModelViewSet, viewsets.ModelViewSet):
    queryset = Wallet.objects.all()
    serializer_class = WalletSerializer
    http_method_names = ["get", "post", "head", "options"]

    @action(detail=True, methods=["post"])
    def deposit(self, request, pk=None):
        wallet = self.get_object()
        serializer = DepositWithdrawSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        body, status_code, replayed = commands.deposit_command(
            tenant=request.tenant,
            wallet_id=wallet.id,
            amount=serializer.validated_data["amount"],
            idempotency_key=serializer.validated_data["idempotency_key"],
        )
        return Response(body, status=status_code, headers={"Idempotent-Replayed": str(replayed)})

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        wallet = self.get_object()
        serializer = DepositWithdrawSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        body, status_code, replayed = commands.withdraw_command(
            tenant=request.tenant,
            wallet_id=wallet.id,
            amount=serializer.validated_data["amount"],
            idempotency_key=serializer.validated_data["idempotency_key"],
        )
        return Response(body, status=status_code, headers={"Idempotent-Replayed": str(replayed)})

    @action(detail=True, methods=["get"])
    def transactions(self, request, pk=None):
        wallet = self.get_object()
        queryset = Transaction.objects.filter(tenant=request.tenant, wallet=wallet)

        page = self.paginate_queryset(queryset)
        serializer = TransactionSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)


class TransferView(APIView):
    def post(self, request):
        serializer = TransferSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        body, status_code, replayed = commands.transfer_command(
            tenant=request.tenant,
            from_wallet_id=data["from_wallet"].id,
            to_wallet_id=data["to_wallet"].id,
            amount=data["amount"],
            idempotency_key=data["idempotency_key"],
        )
        return Response(body, status=status_code, headers={"Idempotent-Replayed": str(replayed)})
