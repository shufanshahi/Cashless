from rest_framework import viewsets

from tenants.mixins import TenantScopedModelViewSet

from .models import Wallet, WalletOwner
from .serializers import WalletOwnerSerializer, WalletSerializer


class WalletOwnerViewSet(TenantScopedModelViewSet, viewsets.ModelViewSet):
    queryset = WalletOwner.objects.all()
    serializer_class = WalletOwnerSerializer
    http_method_names = ["get", "post", "head", "options"]


class WalletViewSet(TenantScopedModelViewSet, viewsets.ModelViewSet):
    queryset = Wallet.objects.all()
    serializer_class = WalletSerializer
    http_method_names = ["get", "post", "head", "options"]
