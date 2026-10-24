"""Move historical imaging sequences into the configurable recording schema."""
from django.db import migrations


def move_sequences(apps, schema_editor):
    alias = schema_editor.connection.alias
    RecordingType = apps.get_model("lab", "RecordingType")
    RecordingField = apps.get_model("lab", "RecordingField")
    Recording = apps.get_model("lab", "Recording")
    Draft = apps.get_model("lab", "SpreadsheetImportDraft")
    imaging = RecordingType.objects.using(alias).filter(slug="imaging").first()
    if imaging is not None:
        field, _ = RecordingField.objects.using(alias).get_or_create(
            recording_type_id=imaging.pk, key="sequence_number",
            defaults={
                "label": "Sequence", "data_type": "integer", "required": False,
                "default_value": 1, "active": True, "display_in_table": True,
                "filterable": False, "order": 0,
            },
        )
        if field.data_type != "integer":
            raise RuntimeError("The imaging sequence_number field must be an integer before migrating legacy sequences.")
        for recording in Recording.objects.using(alias).filter(recording_type_id=imaging.pk).iterator(chunk_size=1000):
            values = dict(recording.values)
            current = values.get("sequence_number")
            if current not in (None, "") and (type(current) is not int or current != recording.sequence_number):
                raise RuntimeError(f"Recording {recording.pk} has conflicting legacy and configurable sequence values. Resolve them before migrating.")
            values["sequence_number"] = recording.sequence_number
            Recording.objects.using(alias).filter(pk=recording.pk).update(values=values)

    # Draft mappings use stable field keys too. Discard stale reviews so they
    # must be refreshed against the new recording schema before submission.
    for draft in Draft.objects.using(alias).filter(completed_at__isnull=True).iterator(chunk_size=1000):
        configuration = dict(draft.configuration)
        for section in ("bindings", "defaults"):
            entries = dict(configuration.get(section, {}))
            if "sequence_number" in entries:
                legacy = entries.pop("sequence_number")
                if imaging is not None and configuration.get("recording_type") == "imaging":
                    if "field:sequence_number" in entries and entries["field:sequence_number"] != legacy:
                        raise RuntimeError(f"Import draft {draft.pk} has conflicting sequence mappings. Resolve them before migrating.")
                    entries["field:sequence_number"] = legacy
                configuration[section] = entries
        state = dict(draft.state)
        state.pop("review_fingerprint", None)
        Draft.objects.using(alias).filter(pk=draft.pk).update(configuration=configuration, state=state)


def restore_imaging_sequences(apps, schema_editor):
    """Restore imaging on rollback; other types receive the old default of 1."""
    Recording = apps.get_model("lab", "Recording")
    alias = schema_editor.connection.alias
    for recording in Recording.objects.using(alias).filter(recording_type__slug="imaging").iterator(chunk_size=1000):
        number = recording.values.get("sequence_number")
        if number is None or number == "":
            continue
        if type(number) is not int or not 0 <= number <= 2147483647:
            raise RuntimeError(f"Recording {recording.pk} has a sequence that cannot be restored to the legacy positive integer column.")
        Recording.objects.using(alias).filter(pk=recording.pk).update(sequence_number=number)


class Migration(migrations.Migration):
    dependencies = [("lab", "0054_spreadsheetimportdraft")]

    operations = [
        migrations.RunPython(move_sequences, reverse_code=restore_imaging_sequences),
        migrations.AlterModelOptions(
            name="recording", options={"ordering": ["-recording_date", "recording_id"]},
        ),
        migrations.RemoveField(model_name="recording", name="sequence_number"),
    ]
