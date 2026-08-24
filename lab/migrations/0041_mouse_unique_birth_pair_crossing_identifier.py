# Generated manually for FLARE-ELN.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0040_backfill_single_line_crossings"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="mouse",
            constraint=models.UniqueConstraint(
                fields=(
                    "date_of_birth",
                    "breeding_pair",
                    "crossing_definition",
                    "tattoo",
                ),
                name="unique_mouse_birth_pair_crossing_identifier",
            ),
        ),
    ]
