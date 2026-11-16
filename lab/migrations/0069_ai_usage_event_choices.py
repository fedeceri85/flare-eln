from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lab", "0068_alter_usageevent_event_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usageevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("code_created", "Code generated"),
                    ("code_reused", "Code reused"),
                    ("code_entered", "Code entered"),
                    ("report_exported", "Report exported"),
                    ("ai_prompt", "AI prompt sent"),
                ],
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="usageevent",
            name="source",
            field=models.CharField(
                choices=[
                    ("sharing", "Sharing"),
                    ("report", "Report export"),
                    ("ai", "AI assistant"),
                ],
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="usageevent",
            name="target_type",
            field=models.CharField(
                choices=[
                    ("animals", "Animals"),
                    ("recording", "Recording"),
                    ("experiment", "Experiment"),
                    ("assistant", "AI assistant"),
                ],
                max_length=16,
            ),
        ),
    ]
