from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("lab", "0071_recordingtype_show_animal_age"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ImageStorageSettings",
        ),
    ]
