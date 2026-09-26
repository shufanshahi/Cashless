from django.http import Http404

from .permissions import IsTenantAuthenticated


class TenantScopedModelViewSet:
    """Base for any viewset whose model has a `tenant` FK.

    Combines three layers of isolation so a bug in one doesn't expose data:
    1. `get_queryset` — every list/retrieve/update/delete is pre-filtered to
       the authenticated tenant, so cross-tenant rows are invisible.
    2. `perform_create` — the tenant is always stamped from the request,
       never accepted from the client payload.
    3. `get_object` — re-checks the object's tenant even though (1) should
       already guarantee it; defense in depth against a queryset override
       upstream forgetting the filter.
    """

    permission_classes = [IsTenantAuthenticated]

    def get_queryset(self):
        return super().get_queryset().filter(tenant=self.request.tenant)

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)

    def get_object(self):
        obj = super().get_object()
        if obj.tenant_id != self.request.tenant.id:
            # 404, not 403 — don't confirm the object exists to another tenant.
            raise Http404
        return obj
