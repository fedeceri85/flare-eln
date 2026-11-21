from django.conf import settings

from .access import user_can_use_ai
from .models import AIProviderSettings


def assistant(request):
    enabled = bool(
        getattr(settings, "FLARE_AI_ENABLED", False)
        and user_can_use_ai(request.user)
    )
    assistant_name = "Marcott-E"
    if enabled:
        try:
            assistant_name = AIProviderSettings.singleton().assistant_name.strip() or "Marcott-E"
        except Exception:
            pass
    return {
        "flare_ai_enabled": enabled,
        "flare_ai_assistant_name": assistant_name,
    }
