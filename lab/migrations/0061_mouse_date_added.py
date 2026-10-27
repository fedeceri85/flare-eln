from django.db import migrations, models
from django.db.models.functions import Coalesce
from django.utils import timezone


def backfill_date_added(apps, schema_editor):
    Mouse = apps.get_model("lab", "Mouse")
    Mouse.objects.using(schema_editor.connection.alias).update(
        date_added=Coalesce("protocol_start_date", "date_of_birth"),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("lab", "0060_remove_recordingimage_caption_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="mouse",
            name="date_added",
            field=models.DateField(
                null=True, blank=True, editable=False, verbose_name="Date added",
            ),
        ),
        migrations.RunPython(backfill_date_added, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="mouse",
            name="date_added",
            field=models.DateField(
                default=timezone.localdate, null=True, blank=True,
                editable=False, verbose_name="Date added",
            ),
        ),
    ]
