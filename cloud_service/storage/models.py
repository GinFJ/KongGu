import uuid

from django.conf import settings
from django.db import models


class AccountIdentity(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)


class AccessToken(models.Model):
    digest = models.CharField(max_length=64, primary_key=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    expires_at = models.DateTimeField(db_index=True)


class Batch(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    batch_id = models.UUIDField()
    title = models.CharField(max_length=100)
    revision = models.PositiveIntegerField()
    updated_at = models.DateTimeField()
    document = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "batch_id"], name="unique_owner_batch")
        ]
