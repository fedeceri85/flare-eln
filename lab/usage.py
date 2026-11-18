"""Minimal usage metadata, without report contents or request fingerprints."""
from django.utils import timezone

from .models import UsageEvent


def record_usage(event_type, user, **details):
    authenticated = user is not None and user.is_authenticated
    return UsageEvent.objects.create(
        event_type=event_type,
        user=user if authenticated else None,
        username=user.get_username() if authenticated else "",
        **details,
    )


def record_daily_ai_usage(user):
    """Record no more than one AI usage event per authenticated user and day."""
    details = {
        "source": "ai",
        "target_type": "assistant",
        "object_count": 1,
    }
    authenticated = user is not None and user.is_authenticated
    if not authenticated:
        return record_usage(UsageEvent.AI_PROMPT, user, **details)
    event, _ = UsageEvent.objects.get_or_create(
        event_type=UsageEvent.AI_PROMPT,
        user=user,
        usage_date=timezone.localdate(),
        defaults={"username": user.get_username(), **details},
    )
    return event


def record_code_usage(link, user, *, created, source, object_count):
    record_usage(
        UsageEvent.CODE_CREATED if created else UsageEvent.CODE_REUSED,
        user, source=source,
        target_type="animals" if link.kind == "m" else "recording",
        reference=link.code,
        object_count=object_count,
    )
