# Generated manually for adding an optional procedure date.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0015_move_procedure_notes_to_mouse"),
    ]

    operations = [
        migrations.AddField(
            model_name="procedure",
            name="date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
