from django.db import migrations

def create_procedure_types(apps, schema_editor):
    ProcedureType = apps.get_model("lab", "ProcedureType")

    names = [
        "Example Procedure Type 1",
        "Example Procedure Type 2",
    ]

    for name in names:
        ProcedureType.objects.get_or_create(name=name)

class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_procedure_types),
    ]