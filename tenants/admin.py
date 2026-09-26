from django.contrib import admin

from .models import Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "api_key", "created_at")
    readonly_fields = ("id", "api_key", "created_at")
