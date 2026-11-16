import json
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from flare_ai.context import resolve_page_context
from flare_ai.views import SESSION_KEY, STARTER_QUESTIONS
from lab.models import Experiment, Project, UsageEvent


class FakeStreamResult:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def stream_text(self, *, delta=False):
        yield "Hello "
        yield "from FLARE."

    def new_messages_json(self):
        return b"[]"


class FakeAgent:
    def __init__(self):
        self.calls = []

    def run_stream_sync(self, message, **kwargs):
        self.calls.append((message, kwargs))
        return FakeStreamResult()


@override_settings(
    ROOT_URLCONF="flare_ai.tests.urls",
    FLARE_AI_ENABLED=True,
    FLARE_AI_GROUP_NAME="FLARE AI test users",
)
class ChatEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="ai-chat-user", password="test-password"
        )
        cls.ai_group = Group.objects.create(name="FLARE AI test users")

    def setUp(self):
        self.client.force_login(self.user)

    def grant_access(self):
        self.user.groups.add(self.ai_group)

    def post_stream(self, **overrides):
        payload = {
            "conversation_id": str(uuid4()),
            "message": "What is on this page?",
            "page_path": "/mice/",
            **overrides,
        }
        return self.client.post(
            reverse("flare_ai:chat_stream"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_group_membership_is_enforced(self):
        self.assertEqual(self.post_stream().status_code, 403)
        response = self.client.get(reverse("lab:home"))
        self.assertNotContains(response, "data-flare-ai-chat")

    @override_settings(FLARE_AI_GROUP_NAME="Missing AI group")
    def test_missing_configured_group_fails_closed(self):
        self.grant_access()
        self.assertEqual(self.post_stream().status_code, 403)

    def test_superuser_without_group_membership_is_rejected(self):
        superuser = get_user_model().objects.create_superuser(
            username="ai-superuser", password="test-password"
        )
        self.client.force_login(superuser)
        self.assertEqual(self.post_stream().status_code, 403)

    def test_streams_ndjson_and_saves_bounded_session_history(self):
        self.grant_access()
        fake = FakeAgent()
        conversation_id = str(uuid4())
        with patch("flare_ai.views.get_agent", return_value=fake):
            response = self.post_stream(conversation_id=conversation_id)
            body = b"".join(response.streaming_content).decode()

        events = [json.loads(line) for line in body.splitlines()]
        self.assertEqual([event["type"] for event in events], ["start", "delta", "delta", "done"])
        self.assertEqual(events[1]["text"], "Hello ")
        self.assertIn(conversation_id, self.client.session[SESSION_KEY])
        self.assertEqual(fake.calls[0][1]["deps"].page.route, "mice_page")
        usage = UsageEvent.objects.get(event_type=UsageEvent.AI_PROMPT)
        self.assertEqual(usage.user, self.user)
        self.assertEqual(usage.username, self.user.username)
        self.assertEqual(usage.source, "ai")
        self.assertEqual(usage.target_type, "assistant")
        self.assertEqual(usage.object_count, 1)
        self.assertEqual(usage.reference, "")
        self.assertEqual(usage.get_event_type_display(), "AI prompt sent")

    def test_provider_failure_still_counts_the_user_attempt(self):
        self.grant_access()
        with (
            patch("flare_ai.views.get_agent", side_effect=RuntimeError("provider failed")),
            patch("flare_ai.views.logger.exception"),
        ):
            response = self.post_stream()
            events = [
                json.loads(line)
                for line in b"".join(response.streaming_content).decode().splitlines()
            ]

        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(UsageEvent.objects.filter(event_type=UsageEvent.AI_PROMPT).count(), 1)

    def test_hidden_greeting_warms_model_without_recording_usage(self):
        self.grant_access()
        fake = FakeAgent()
        with patch("flare_ai.views.get_agent", return_value=fake):
            response = self.post_stream(greeting=True, message="Do not use this text")
            events = [
                json.loads(line)
                for line in b"".join(response.streaming_content).decode().splitlines()
            ]

        self.assertEqual(fake.calls[0][0], "Hello")
        self.assertEqual(events[-1]["suggestions"], STARTER_QUESTIONS)
        self.assertFalse(UsageEvent.objects.exists())

    def test_greeting_is_rejected_after_conversation_has_started(self):
        self.grant_access()
        conversation_id = str(uuid4())
        session = self.client.session
        session[SESSION_KEY] = {conversation_id: ["[]"]}
        session.save()
        response = self.post_stream(conversation_id=conversation_id, greeting=True)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(UsageEvent.objects.exists())

    def test_reset_removes_server_history(self):
        self.grant_access()
        conversation_id = str(uuid4())
        session = self.client.session
        session[SESSION_KEY] = {conversation_id: ["[]"]}
        session.save()
        response = self.client.post(
            reverse("flare_ai:reset_chat"),
            data=json.dumps({"conversation_id": conversation_id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(conversation_id, self.client.session[SESSION_KEY])

    def test_invalid_payload_is_rejected_before_model_call(self):
        self.grant_access()
        response = self.post_stream(conversation_id="not-a-uuid")
        self.assertEqual(response.status_code, 400)

    def test_group_member_sees_global_drawer(self):
        self.grant_access()
        response = self.client.get(reverse("lab:home"))
        self.assertContains(response, "data-flare-ai-chat")
        self.assertContains(response, reverse("flare_ai:chat_stream"))

    @override_settings(FLARE_AI_ASSISTANT_NAME="Lab Helper")
    def test_configured_assistant_name_is_rendered(self):
        self.grant_access()
        response = self.client.get(reverse("lab:home"))
        self.assertContains(response, 'data-assistant-name="Lab Helper"')
        self.assertContains(response, "Ask Lab Helper")
        self.assertContains(response, "Message Lab Helper")
        self.assertNotContains(response, "Marcott-E")


@override_settings(ROOT_URLCONF="flare_ai.tests.urls")
class PageContextTests(TestCase):
    def test_resolves_route_and_related_record_names(self):
        project = Project.objects.create(name="Context project")
        experiment = Experiment.objects.create(project=project, name="Context experiment")
        path = reverse("lab:recordings_page", args=[project.pk, experiment.pk])
        context = resolve_page_context(path)
        self.assertEqual(context.route, "recordings_page")
        self.assertEqual(context.records["project_name"], project.name)
        self.assertEqual(context.records["experiment_name"], experiment.name)

    def test_rejects_external_page_context(self):
        context = resolve_page_context("https://example.org/mice/")
        self.assertEqual(context.route, "unknown")
        self.assertEqual(context.records, {})
