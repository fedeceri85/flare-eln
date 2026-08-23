# Generated manually for allowing repeated procedure types per mouse.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0016_procedure_date"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="procedure",
            name="uq_mouse_procedure",
        ),
    ]
