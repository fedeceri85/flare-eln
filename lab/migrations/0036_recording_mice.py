from django.db import migrations, models


def copy_mouse_to_mice(apps, schema_editor):
    Recording = apps.get_model("lab", "Recording")

    for recording in Recording.objects.exclude(mouse_id__isnull=True):
        recording.mice.add(recording.mouse_id)


def restore_mouse_from_mice(apps, schema_editor):
    Recording = apps.get_model("lab", "Recording")

    for recording in Recording.objects.all():
        mouse = recording.mice.order_by("mouse_id").first()

        if mouse is None:
            continue

        recording.mouse_id = mouse.pk
        recording.save(update_fields=["mouse"])


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0035_protocol_unique_number_per_licence"),
    ]

    operations = [
        migrations.AddField(
            model_name="recording",
            name="mice",
            field=models.ManyToManyField(
                blank=True,
                related_name="recordings",
                to="lab.mouse",
            ),
        ),
        migrations.RunPython(
            copy_mouse_to_mice,
            restore_mouse_from_mice,
        ),
        migrations.RemoveField(
            model_name="recording",
            name="mouse",
        ),
        migrations.AlterModelOptions(
            name="recording",
            options={
                "ordering": [
                    "-recording_date",
                    "sequence_number",
                    "recording_id",
                ],
            },
        ),
    ]
