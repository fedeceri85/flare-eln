# Generated manually for moving procedure notes onto mouse notes.

from django.db import migrations


def move_procedure_notes_to_mouse(apps, schema_editor):
    Mouse = apps.get_model("lab", "Mouse")
    Procedure = apps.get_model("lab", "Procedure")
    ProcedureType = apps.get_model("lab", "ProcedureType")
    db_alias = schema_editor.connection.alias

    procedure_type_names = dict(
        ProcedureType.objects.using(db_alias).values_list(
            "procedure_type_id",
            "name",
        )
    )

    procedures = (
        Procedure.objects
        .using(db_alias)
        .exclude(notes="")
        .iterator()
    )

    for procedure in procedures:
        note = (procedure.notes or "").strip()

        if not note:
            continue

        mouse = Mouse.objects.using(db_alias).get(pk=procedure.mouse_id)
        note_parts = [
            "Procedure note",
            f"Procedure type: {procedure_type_names.get(procedure.procedure_type_id, 'Unknown')}",
        ]

        if procedure.lab_member:
            note_parts.append(f"Lab member: {procedure.lab_member}")

        if procedure.lab_book_page:
            note_parts.append(f"Lab-book page: {procedure.lab_book_page}")

        note_parts.append(note)
        migrated_note = "\n".join(note_parts)
        existing_notes = (mouse.notes or "").rstrip()

        if existing_notes:
            mouse.notes = f"{existing_notes}\n\n{migrated_note}"
        else:
            mouse.notes = migrated_note

        mouse.save(update_fields=["notes"])


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0014_mouse_notes"),
    ]

    operations = [
        migrations.RunPython(
            move_procedure_notes_to_mouse,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="procedure",
            name="notes",
        ),
    ]
