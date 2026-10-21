from django.db import migrations, models


def preserve_crossing_order(apps, schema_editor):
    Crossing = apps.get_model("lab", "Crossing")
    CrossingLine = apps.get_model("lab", "CrossingLine")
    alias = schema_editor.connection.alias
    for crossing in Crossing.objects.using(alias).iterator():
        memberships = list(
            CrossingLine.objects.using(alias).filter(crossing_id=crossing.pk)
            .select_related("line").order_by("crossing_line_id")
        )
        # Keep the existing displayed order when it can be matched exactly.
        by_name = {membership.line.name: membership for membership in memberships}
        names = crossing.display_name.split(" x ")
        if len(names) == len(memberships) and set(names) == set(by_name):
            memberships = [by_name[name] for name in names]
        for position, membership in enumerate(memberships):
            membership.position = position
        CrossingLine.objects.using(alias).bulk_update(memberships, ["position"])


class Migration(migrations.Migration):
    dependencies = [("lab", "0043_recording_data_paths")]

    operations = [
        migrations.AddField(
            model_name="crossingline",
            name="position",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(preserve_crossing_order, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name="crossingline",
            options={"ordering": ["crossing", "position", "crossing_line_id"]},
        ),
    ]
