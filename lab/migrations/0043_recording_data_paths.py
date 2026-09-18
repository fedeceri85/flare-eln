from django.db import migrations, models


def parse_recording_paths(value):
    paths = []

    for line in (value or "").splitlines():
        path = line.strip()

        if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
            path = path[1:-1]

        if path:
            paths.append(path)

    return paths


def migrate_data_paths(apps, schema_editor):
    Recording = apps.get_model("lab", "Recording")

    for recording in Recording.objects.all().iterator():
        recording.data_paths = parse_recording_paths(recording.data_path)
        recording.save(update_fields=["data_paths"])


def restore_data_path(apps, schema_editor):
    Recording = apps.get_model("lab", "Recording")

    for recording in Recording.objects.all().iterator():
        recording.data_path = "\n".join(recording.data_paths)
        recording.save(update_fields=["data_path"])


class Migration(migrations.Migration):
    dependencies = [
        ("lab", "0042_login_event"),
    ]

    operations = [
        migrations.AddField(
            model_name="recording",
            name="data_paths",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterField(
            model_name="recording",
            name="data_path",
            field=models.TextField(null=True),
        ),
        migrations.RunPython(
            migrate_data_paths,
            reverse_code=restore_data_path,
        ),
        migrations.RemoveField(
            model_name="recording",
            name="data_path",
        ),
    ]
