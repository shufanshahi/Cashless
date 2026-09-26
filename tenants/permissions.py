from rest_framework.permissions import BasePermission


class IsTenantAuthenticated(BasePermission):
    """Requires a tenant to have been resolved by TenantAPIKeyAuthentication."""

    message = "A valid API key or X-Tenant-ID header is required."

    def has_permission(self, request, view):
        return getattr(request, "tenant", None) is not None
