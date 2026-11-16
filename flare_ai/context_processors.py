from django.conf import settings

from .access import user_can_use_ai


def assistant(request):
    enabled = bool(
        getattr(settings, "FLARE_AI_ENABLED", False)
        and user_can_use_ai(request.user)
    )
    return {
        "flare_ai_enabled": enabled,
        "flare_ai_assistant_name": getattr(
            settings, "FLARE_AI_ASSISTANT_NAME", "Marcott-E"
        ),
    }
