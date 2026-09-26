from rest_framework.routers import DefaultRouter

from .views import WalletOwnerViewSet, WalletViewSet

router = DefaultRouter()
router.register("wallet-owners", WalletOwnerViewSet, basename="wallet-owner")
router.register("wallets", WalletViewSet, basename="wallet")

urlpatterns = router.urls
