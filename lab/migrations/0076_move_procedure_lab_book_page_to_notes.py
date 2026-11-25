# Generated manually for moving the procedure lab-book page into procedure notes.

from django.db import migrations


def move_lab_book_page_to_notes(apps, schema_editor):
    """Copy each procedure's lab-book page into its notes, formatted as
    "Lab book page: {page}". Existing note text is kept and the page line is
    appended on a new line."""
    Procedure = apps.get_model("lab", "Procedure")
    db_alias = schema_editor.connection.alias

    procedures = (
        Procedure.objects
        .using(db_alias)
        .exclude(lab_book_page="")
        .iterator()
    )

    for procedure in procedures:
        page = procedure.lab_book_page.strip()

        if not page:
            continue

        line = f"Lab book page: {page}"
        existing_notes = (procedure.notes or "").strip()

        if existing_notes:
            procedure.notes = f"{existing_notes}\n{line}"
        else:
            procedure.notes = line

        procedure.save(update_fields=["notes"])


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0075_remove_legacy_recording_crossing_values"),
    ]

    operations = [
        migrations.RunPython(
            move_lab_book_page_to_notes,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="procedure",
            name="lab_book_page",
        ),
    ]
