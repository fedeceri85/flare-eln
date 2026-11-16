from django.conf import settings


def user_can_use_ai(user):
    """Return whether an authenticated user belongs to the configured AI group."""
    group_name = getattr(settings, "FLARE_AI_GROUP_NAME", "").strip()
    return bool(
        group_name
        and user.is_authenticated
        and user.groups.filter(name=group_name).exists()
    )
