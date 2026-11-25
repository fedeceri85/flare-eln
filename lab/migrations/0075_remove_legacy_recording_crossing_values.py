from django.db import migrations


def remove_legacy_recording_crossing_values(apps, schema_editor):
    """Remove obsolete recording-level crossings after validating animals.

    Genotyping crossings are derived exclusively from linked animals. Abort the
    migration before changing anything if a recording has no animals or if a
    linked animal has neither a structured nor a legacy crossing.

    Note: the reverse migration is a no-op. Once applied, the legacy
    ``values["crossing"]`` data is permanently removed and cannot be restored by
    rolling back; keep a database backup if you need to undo this.
    """
    RecordingType = apps.get_model("lab", "RecordingType")
    Recording = apps.get_model("lab", "Recording")

    genotyping = RecordingType.objects.filter(slug="genotyping").first()
    if genotyping is None:
        return

    recordings = list(
        Recording.objects.filter(recording_type=genotyping).prefetch_related("mice")
    )
    without_animals = []
    missing_crossings = []

    for recording in recordings:
        mice = list(recording.mice.all())
        if not mice:
            without_animals.append(recording.pk)
            continue

        missing_mouse_ids = [
            str(mouse.pk)
            for mouse in mice
            if not mouse.crossing_definition_id
            and not str(mouse.crossing or "").strip()
        ]
        if missing_mouse_ids:
            missing_crossings.append((recording.pk, missing_mouse_ids))

    if without_animals or missing_crossings:
        details = []
        if without_animals:
            details.append(
                "recordings without animals: "
                + ", ".join(str(pk) for pk in without_animals)
            )
        if missing_crossings:
            details.append(
                "recordings with animals missing crossing data: "
                + "; ".join(
                    f"{recording_pk} ({', '.join(mouse_ids)})"
                    for recording_pk, mouse_ids in missing_crossings
                )
            )
        raise RuntimeError(
            "Cannot remove legacy genotyping crossing values; " + "; ".join(details)
        )

    for recording in recordings:
        if "crossing" not in recording.values:
            continue

        recording.values = dict(recording.values)
        recording.values.pop("crossing", None)
        recording.save(update_fields=["values"])


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0074_remove_genotyping_crossing_field"),
    ]

    operations = [
        migrations.RunPython(
            remove_legacy_recording_crossing_values,
            migrations.RunPython.noop,
        ),
    ]
