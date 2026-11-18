from django.db import migrations, models
from django.utils import timezone


def collapse_ai_usage_by_user_and_day(apps, schema_editor):
    UsageEvent = apps.get_model("lab", "UsageEvent")
    seen = set()
    for event in UsageEvent.objects.filter(event_type="ai_prompt").order_by(
        "created_at", "pk"
    ).iterator():
        usage_date = timezone.localdate(event.created_at)
        user_key = event.user_id if event.user_id is not None else event.username
        key = (user_key, usage_date)
        if key in seen:
            event.delete()
            continue
        seen.add(key)
        event.usage_date = usage_date
        event.save(update_fields=["usage_date"])


class Migration(migrations.Migration):
    dependencies = [
        ("lab", "0069_ai_usage_event_choices"),
    ]

    operations = [
        migrations.AddField(
            model_name="usageevent",
            name="usage_date",
            field=models.DateField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(
            collapse_ai_usage_by_user_and_day,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="usageevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("code_created", "Code generated"),
                    ("code_reused", "Code reused"),
                    ("code_entered", "Code entered"),
                    ("report_exported", "Report exported"),
                    ("ai_prompt", "AI assistant used"),
                ],
                max_length=24,
            ),
        ),
        migrations.AddConstraint(
            model_name="usageevent",
            constraint=models.UniqueConstraint(
                condition=models.Q(("event_type", "ai_prompt")),
                fields=("event_type", "user", "usage_date"),
                name="usage_ai_user_day_uniq",
            ),
        ),
    ]
