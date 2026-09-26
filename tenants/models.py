import secrets
import uuid

from django.db import models


def generate_api_key():
    return secrets.token_hex(32)


class Tenant(models.Model):
    # Lets a Tenant stand in for `request.user` once authenticated, so DRF's
    # IsAuthenticated-style checks work without a real auth.User per tenant.
    is_authenticated = True

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    api_key = models.CharField(max_length=64, unique=True, default=generate_api_key, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
