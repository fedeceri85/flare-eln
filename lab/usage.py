"""Minimal usage metadata, without report contents or request fingerprints."""
from .models import UsageEvent


def record_usage(event_type, user, **details):
    authenticated = user is not None and user.is_authenticated
    return UsageEvent.objects.create(
        event_type=event_type,
        user=user if authenticated else None,
        username=user.get_username() if authenticated else "",
        **details,
    )


def record_code_usage(link, user, *, created, source, object_count):
    record_usage(
        UsageEvent.CODE_CREATED if created else UsageEvent.CODE_REUSED,
        user, source=source,
        target_type="animals" if link.kind == "m" else "recording",
        reference=link.code,
        object_count=object_count,
    )
