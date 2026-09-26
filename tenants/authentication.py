from django.core.exceptions import ValidationError
from rest_framework import authentication, exceptions

from .models import Tenant


class TenantAPIKeyAuthentication(authentication.BaseAuthentication):
    """Resolves the tenant for a request from either credential.

    Primary: ``Authorization: Api-Key <key>`` — a real secret, safe to use as
    the sole credential.

    Fallback: ``X-Tenant-ID: <uuid>`` — convenient for local/dev testing, but
    NOT a security boundary on its own since tenant IDs aren't secret. Real
    deployments should require the API key.
    """

    def authenticate(self, request):
        tenant = self._authenticate_via_api_key(request)
        if tenant is None:
            tenant = self._authenticate_via_tenant_id_header(request)
        if tenant is None:
            return None

        request.tenant = tenant
        return (tenant, None)

    def _authenticate_via_api_key(self, request):
        auth_header = authentication.get_authorization_header(request).decode("utf-8")
        if not auth_header:
            return None

        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "api-key":
            return None

        try:
            return Tenant.objects.get(api_key=parts[1])
        except Tenant.DoesNotExist:
            raise exceptions.AuthenticationFailed("Invalid API key.")

    def _authenticate_via_tenant_id_header(self, request):
        tenant_id = request.META.get("HTTP_X_TENANT_ID")
        if not tenant_id:
            return None

        try:
            return Tenant.objects.get(id=tenant_id)
        except (Tenant.DoesNotExist, ValidationError, ValueError):
            raise exceptions.AuthenticationFailed("Invalid X-Tenant-ID.")

    def authenticate_header(self, request):
        return "Api-Key"
