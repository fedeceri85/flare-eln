from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("lab", "0028_experiment_recording_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="recording",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="recordings",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
