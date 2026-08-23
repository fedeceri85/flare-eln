from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0034_alter_mouse_protocol"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="protocol",
            constraint=models.UniqueConstraint(
                fields=("licence_reference", "protocol_number"),
                name="unique_protocol_number_per_licence",
            ),
        ),
    ]
