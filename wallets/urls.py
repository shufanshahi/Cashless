from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import TransferView, WalletOwnerViewSet, WalletViewSet

router = DefaultRouter()
router.register("wallet-owners", WalletOwnerViewSet, basename="wallet-owner")
router.register("wallets", WalletViewSet, basename="wallet")

urlpatterns = [
    path("transfers/", TransferView.as_view(), name="transfer-create"),
] + router.urls
