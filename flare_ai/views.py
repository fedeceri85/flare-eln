import json
import logging
from uuid import UUID

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_POST
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.usage import UsageLimits

from lab.models import UsageEvent
from lab.usage import record_usage

from .access import user_can_use_ai
from .agent import get_agent
from .context import AIRequestContext, resolve_page_context


logger = logging.getLogger(__name__)
SESSION_KEY = "flare_ai_conversations"
MAX_TURNS = 10
MAX_CONVERSATIONS = 5
MAX_MESSAGE_LENGTH = 4000
GREETING_MESSAGE = "Hello"
STARTER_QUESTIONS = [
    "What can you help me with?",
    "How can I search for animals and recordings?",
    "What makes this AI assistant possible?",
]


def _event(kind, **payload):
    return json.dumps({"type": kind, **payload}, separators=(",", ":")) + "\n"


def _payload(request):
    if request.content_type != "application/json":
        raise ValueError("Expected a JSON request.")
    try:
        value = json.loads(request.body)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid JSON request.") from exc
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


def _conversation_id(value):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Invalid conversation ID.") from exc


def _require_access(request):
    if not getattr(settings, "FLARE_AI_ENABLED", False):
        raise PermissionDenied
    if not user_can_use_ai(request.user):
        raise PermissionDenied


def _history(turns):
    messages = []
    for turn in turns:
        try:
            messages.extend(ModelMessagesTypeAdapter.validate_json(turn))
        except Exception:
            logger.warning("Discarded invalid FLARE AI session history.")
            return []
    return messages


@login_required(login_url="lab:login")
@require_POST
def chat_stream(request):
    _require_access(request)
    try:
        payload = _payload(request)
        conversation_id = _conversation_id(payload.get("conversation_id"))
        greeting = payload.get("greeting") is True
        message = GREETING_MESSAGE if greeting else payload.get("message")
        page_path = payload.get("page_path", "/")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Enter a message.")
        message = message.strip()
        if len(message) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"Messages are limited to {MAX_MESSAGE_LENGTH} characters.")
        if not isinstance(page_path, str) or len(page_path) > 2048:
            raise ValueError("Invalid page path.")
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    conversations = request.session.get(SESSION_KEY, {})
    if not isinstance(conversations, dict):
        conversations = {}
    turns = conversations.get(conversation_id, [])
    if not isinstance(turns, list):
        turns = []
    if greeting and turns:
        return JsonResponse({"error": "This conversation has already started."}, status=409)
    history = _history(turns[-MAX_TURNS:])
    deps = AIRequestContext(
        user=request.user,
        page=resolve_page_context(page_path),
    )

    def stream():
        yield _event("start", conversation_id=conversation_id)
        try:
            if not greeting:
                record_usage(
                    UsageEvent.AI_PROMPT,
                    request.user,
                    source="ai",
                    target_type="assistant",
                    object_count=1,
                )
            with get_agent().run_stream_sync(
                message,
                message_history=history,
                conversation_id=conversation_id,
                deps=deps,
                usage_limits=UsageLimits(request_limit=8),
                timeout=300,
            ) as result:
                for delta in result.stream_text(delta=True):
                    if delta:
                        yield _event("delta", text=delta)
                turn = result.new_messages_json().decode("utf-8")

            stored = request.session.get(SESSION_KEY, {})
            if not isinstance(stored, dict):
                stored = {}
            stored.pop(conversation_id, None)
            stored[conversation_id] = [*turns, turn][-MAX_TURNS:]
            while len(stored) > MAX_CONVERSATIONS:
                stored.pop(next(iter(stored)))
            request.session[SESSION_KEY] = stored
            request.session.modified = True
            # Session middleware has already returned the streaming response.
            request.session.save()
            yield _event(
                "done",
                conversation_id=conversation_id,
                suggestions=STARTER_QUESTIONS if greeting else [],
            )
        except GeneratorExit:
            raise
        except Exception:
            logger.exception("FLARE AI request failed without logging prompt content.")
            yield _event(
                "error",
                code="assistant_unavailable",
                message="The assistant could not complete this request. Please try again.",
            )

    response = StreamingHttpResponse(stream(), content_type="application/x-ndjson")
    response["Cache-Control"] = "private, no-store"
    response["X-Accel-Buffering"] = "no"
    return response


@login_required(login_url="lab:login")
@require_POST
def reset_chat(request):
    _require_access(request)
    try:
        conversation_id = _conversation_id(_payload(request).get("conversation_id"))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    conversations = request.session.get(SESSION_KEY, {})
    if isinstance(conversations, dict):
        conversations.pop(conversation_id, None)
        request.session[SESSION_KEY] = conversations
        request.session.modified = True
    return JsonResponse({"reset": True})
