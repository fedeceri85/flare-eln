# Generated manually for FLARE-ELN.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("lab", "0041_mouse_unique_birth_pair_crossing_identifier"),
    ]

    operations = [
        migrations.CreateModel(
            name="LoginEvent",
            fields=[
                (
                    "login_event_id",
                    models.AutoField(primary_key=True, serialize=False),
                ),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("login", "Login"),
                            ("logout", "Logout"),
                            ("failed", "Failed login"),
                        ],
                        max_length=20,
                    ),
                ),
                ("username", models.CharField(blank=True, max_length=150)),
                (
                    "ip_address",
                    models.GenericIPAddressField(blank=True, null=True),
                ),
                ("user_agent", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="login_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["-created_at"],
                        name="login_event_created_at_idx",
                    ),
                    models.Index(
                        fields=["event_type", "-created_at"],
                        name="login_event_type_created_idx",
                    ),
                    models.Index(
                        fields=["username"],
                        name="login_event_username_idx",
                    ),
                ],
            },
        ),
    ]
