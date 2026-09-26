from rest_framework import generics, permissions

from .models import Tenant
from .serializers import TenantSerializer


class TenantCreateView(generics.CreateAPIView):
    """Unauthenticated bootstrapping endpoint: returns the api_key once."""

    queryset = Tenant.objects.all()
    serializer_class = TenantSerializer
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
