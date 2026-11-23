import json
import logging
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from pydantic_ai.messages import BaseToolCallPart, ImageUrl, ModelMessagesTypeAdapter
from pydantic_ai.usage import UsageLimits

from lab.usage import record_daily_ai_usage

from .access import user_can_use_ai
from .agent import get_agent
from .attachments import (
    attachment_from_files,
    attachment_upload_was_too_large,
    install_attachment_upload_limit,
)
from .context import AIRequestContext, resolve_page_context
from .forms import AIProviderSettingsForm
from .models import AIProviderSettings


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
    if request.content_type == "application/json":
        try:
            value = json.loads(request.body)
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid JSON request.") from exc
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object.")
        return value
    if request.content_type == "multipart/form-data":
        value = request.POST.dict()
        if attachment_upload_was_too_large(request):
            raise ValidationError("Attach a file no larger than 10 MB.")
        return value
    raise ValueError("Expected a JSON or multipart request.")


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


def _tools_used(result):
    """Raw tool names the model called in this reply, in first-use order with repeat counts.

    Only this run's messages are inspected, so tool calls from earlier turns
    (replayed via message history) are not accumulated.
    """
    counts = {}
    for message in result.new_messages():
        for part in getattr(message, "parts", ()):
            if isinstance(part, BaseToolCallPart) and part.tool_name:
                counts[part.tool_name] = counts.get(part.tool_name, 0) + 1
    return [{"name": name, "count": count} for name, count in counts.items()]


@login_required(login_url="lab:login")
@require_POST
def chat_stream(request):
    _require_access(request)
    attachment = None
    try:
        if request.content_type == "multipart/form-data":
            install_attachment_upload_limit(request)
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
        attachment = attachment_from_files(request.FILES)
        if greeting and attachment is not None:
            raise ValueError("Files cannot be attached to the hidden greeting.")
    except (ValueError, ValidationError) as exc:
        if attachment is not None:
            attachment.close()
        message = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
        return JsonResponse({"error": message}, status=400)

    if request.FILES and attachment is None:
        return JsonResponse({"error": "Use the attachment field for chat files."}, status=400)

    if attachment is not None and greeting:
        attachment.close()
        attachment = None
        return JsonResponse({"error": "Files cannot be attached to the hidden greeting."}, status=400)

    try:
        conversation_id = _conversation_id(conversation_id)
    except ValueError as exc:
        if attachment is not None:
            attachment.close()
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
        session=request.session,
        attachment=attachment,
    )

    # Build the user prompt: text-only or multi-modal (text + image(s)).
    user_prompt = message
    if attachment is not None:
        try:
            image_urls = attachment.image_data_urls()
        except ValidationError as exc:
            attachment.close()
            return JsonResponse({"error": exc.messages[0]}, status=400)
        if image_urls:
            user_prompt = [message, *[ImageUrl(url=u) for u in image_urls]]

    def stream():
        try:
            yield _event("start", conversation_id=conversation_id)
            try:
                if not greeting:
                    record_daily_ai_usage(request.user)
                with get_agent().run_stream_sync(
                    user_prompt,
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
                    try:
                        tools = _tools_used(result)
                    except Exception:
                        logger.exception("FLARE AI tool extraction failed; continuing without tool details.")
                        tools = []

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
                    tools=tools,
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
        finally:
            if attachment is not None:
                attachment.close()

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


@login_required(login_url="lab:login")
def ai_settings(request):
    """Staff-only page to configure the AI assistant provider connection."""
    if not request.user.is_staff:
        raise PermissionDenied
    instance = AIProviderSettings.singleton()
    if request.method == "POST":
        form = AIProviderSettingsForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            get_agent.cache_clear()
            messages.success(request, "AI assistant settings saved.")
            return redirect("flare_ai:ai_settings")
    else:
        form = AIProviderSettingsForm(instance=instance)
    key_status = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022" if instance.has_api_key else "not set"
    return render(request, "flare_ai/ai_settings.html", {
        "form": form,
        "key_status": key_status,
    })
