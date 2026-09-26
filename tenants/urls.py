from django.urls import path

from .views import TenantCreateView

urlpatterns = [
    path("tenants/", TenantCreateView.as_view(), name="tenant-create"),
]
