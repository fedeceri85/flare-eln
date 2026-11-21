"""Singleton settings for the AI assistant provider connection."""
from django.db import models


class AIProviderSettings(models.Model):
    """Singleton row (id=1) holding AI provider configuration.

    Values are configured through the admin Settings page, not ``.env``.
    The API key is stored in plain text in the database and must never
    be rendered in any template or response body.
    """
    base_url = models.CharField(max_length=500, blank=True)
    api_key = models.CharField(max_length=1000, blank=True)
    model_name = models.CharField(max_length=200, blank=True)
    assistant_name = models.CharField(max_length=100, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "AI Provider Settings"
        verbose_name_plural = "AI Provider Settings"

    def __str__(self):
        return "AI Provider Settings"

    def save(self, *args, **kwargs):
        """Force singleton: always use id=1."""
        self.id = 1
        super().save(*args, **kwargs)

    @classmethod
    def singleton(cls):
        instance, _ = cls.objects.get_or_create(id=1)
        return instance

    @property
    def has_api_key(self):
        return bool(self.api_key.strip())

    @property
    def api_key_suffix(self):
        """Last 4 characters of the key for masked display."""
        key = self.api_key.strip()
        return key[-4:] if len(key) >= 4 else ""