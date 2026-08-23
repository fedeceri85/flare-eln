from django.db import migrations


def backfill_single_line_crossings(apps, schema_editor):
    Crossing = apps.get_model("lab", "Crossing")
    CrossingLine = apps.get_model("lab", "CrossingLine")
    MouseLine = apps.get_model("lab", "MouseLine")

    for line in MouseLine.objects.select_related("species").iterator():
        crossing, _ = Crossing.objects.get_or_create(
            species_id=line.species_id,
            canonical_key=str(line.pk),
            defaults={
                "display_name": line.name,
                "active": True,
            },
        )

        if crossing.display_name != line.name:
            crossing.display_name = line.name
            crossing.save(update_fields=["display_name"])

        CrossingLine.objects.get_or_create(
            crossing_id=crossing.pk,
            line_id=line.pk,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0039_rename_multiple_crossing_type"),
    ]

    operations = [
        migrations.RunPython(
            backfill_single_line_crossings,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
