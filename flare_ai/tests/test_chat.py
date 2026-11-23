import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadhandler import StopUpload
from django.core.files.uploadedfile import SimpleUploadedFile, UploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from django.urls import reverse
from django.utils.datastructures import MultiValueDict
from django.utils import timezone
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from flare_ai.attachments import (
    MAX_ATTACHMENT_BYTES,
    MAX_MULTIPART_REQUEST_BYTES,
    AttachmentSizeUploadHandler,
    RequestAttachment,
    attachment_from_files,
    attachment_upload_was_too_large,
)
from flare_ai.context import resolve_page_context
from flare_ai.views import SESSION_KEY, STARTER_QUESTIONS
from lab.models import Experiment, Project, UsageEvent


class FakeStreamResult:
    def __init__(self, messages=None, history=None):
        if messages is None:
            messages = [
                ModelResponse(parts=[
                    ToolCallPart(tool_name="search_mice"),
                    ToolCallPart(tool_name="search_mice"),
                ]),
                ModelResponse(parts=[ToolCallPart(tool_name="user_guide")]),
            ]
        self._new_messages = messages
        self._history = history or []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def stream_text(self, *, delta=False):
        yield "Hello "
        yield "from FLARE."

    def new_messages_json(self):
        return b"[]"

    def new_messages(self):
        return self._new_messages

    def all_messages(self):
        return [*self._history, *self._new_messages]


class FakeAgent:
    def __init__(self, messages=None, history=None):
        self.messages = messages
        self.history = history
        self.calls = []

    def run_stream_sync(self, message, **kwargs):
        self.calls.append((message, kwargs))
        return FakeStreamResult(self.messages, self.history)


class FailingAgent:
    def __init__(self):
        self.calls = []

    def run_stream_sync(self, message, **kwargs):
        self.calls.append((message, kwargs))
        raise RuntimeError("provider exploded")


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
        self.assertIsNotNone(fake.calls[0][1]["deps"].session)
        usage = UsageEvent.objects.get(event_type=UsageEvent.AI_PROMPT)
        self.assertEqual(usage.user, self.user)
        self.assertEqual(usage.username, self.user.username)
        self.assertEqual(usage.source, "ai")
        self.assertEqual(usage.target_type, "assistant")
        self.assertEqual(usage.object_count, 1)
        self.assertEqual(usage.reference, "")
        self.assertEqual(usage.get_event_type_display(), "AI assistant used")

    def test_done_event_reports_only_tools_used_in_current_reply(self):
        self.grant_access()
        history = [
            ModelRequest(parts=[UserPromptPart("previous question")]),
            ModelResponse(parts=[ToolCallPart(tool_name="search_mice"), ToolCallPart(tool_name="user_guide")]),
            ModelRequest(parts=[
                ToolReturnPart(tool_name="search_mice", content="ok"),
                ToolReturnPart(tool_name="user_guide", content="ok"),
            ]),
            ModelResponse(parts=[TextPart("previous answer")]),
        ]
        with patch("flare_ai.views.get_agent", return_value=FakeAgent(history=history)):
            response = self.post_stream()
            events = [
                json.loads(line)
                for line in b"".join(response.streaming_content).decode().splitlines()
            ]

        # Tool calls from the previous turn must not accumulate into this reply.
        self.assertEqual(
            events[-1]["tools"],
            [
                {"name": "search_mice", "count": 2},
                {"name": "user_guide", "count": 1},
            ],
        )

    def test_tool_returns_are_not_reported_as_tool_calls(self):
        self.grant_access()
        fake = FakeAgent(
            messages=[ModelRequest(parts=[ToolReturnPart(tool_name="search_mice", content="ok")])]
        )
        with patch("flare_ai.views.get_agent", return_value=fake):
            response = self.post_stream()
            events = [
                json.loads(line)
                for line in b"".join(response.streaming_content).decode().splitlines()
            ]

        self.assertEqual(events[-1]["tools"], [])

    def test_usage_events_are_limited_per_user_and_day(self):
        self.grant_access()
        fake = FakeAgent()
        today = timezone.localdate()
        with patch("flare_ai.views.get_agent", return_value=fake):
            with patch("lab.usage.timezone.localdate", return_value=today):
                for _ in range(2):
                    response = self.post_stream()
                    b"".join(response.streaming_content)

                other = get_user_model().objects.create_user(username="other-ai-user")
                other.groups.add(self.ai_group)
                self.client.force_login(other)
                response = self.post_stream()
                b"".join(response.streaming_content)

            self.client.force_login(self.user)
            with patch(
                "lab.usage.timezone.localdate", return_value=today + timedelta(days=1)
            ):
                response = self.post_stream()
                b"".join(response.streaming_content)

        events = UsageEvent.objects.filter(event_type=UsageEvent.AI_PROMPT)
        self.assertEqual(events.count(), 3)
        self.assertEqual(
            events.filter(user=self.user, usage_date=today).count(),
            1,
        )
        self.assertEqual(
            events.filter(user=self.user, usage_date=today + timedelta(days=1)).count(),
            1,
        )
        self.assertEqual(events.filter(user=other, usage_date=today).count(), 1)

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

    def test_configured_assistant_name_is_rendered(self):
        self.grant_access()
        from flare_ai.models import AIProviderSettings
        cfg = AIProviderSettings.singleton()
        cfg.assistant_name = "Lab Helper"
        cfg.save()
        response = self.client.get(reverse("lab:home"))
        self.assertContains(response, 'data-assistant-name="Lab Helper"')
        self.assertContains(response, "Ask Lab Helper")
        self.assertContains(response, "Message Lab Helper")
        self.assertNotContains(response, "Marcott-E")

    # -- Request-scoped file attachments ---------------------------------
    def post_multipart(self, *, name="data.csv", content=b"a,b,c\n1,2,3", content_type="text/csv", **overrides):
        data = {
            "conversation_id": str(uuid4()),
            "message": "What is this?",
            "page_path": "/mice/",
            "attachment": SimpleUploadedFile(name, content, content_type=content_type),
        }
        data.update(overrides)
        return self.client.post(reverse("flare_ai:chat_stream"), data=data)

    def test_multipart_prompt_accepts_all_supported_extensions(self):
        self.grant_access()
        fake = FakeAgent()
        cases = [
            ("table.csv", "text/csv"),
            ("book.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("notes.txt", "text/plain"),
            ("readme.md", "text/markdown"),
        ]
        for name, content_type in cases:
            with patch("flare_ai.views.get_agent", return_value=fake):
                response = self.post_multipart(name=name, content_type=content_type)
                self.assertEqual(response.status_code, 200, name)
                body = b"".join(response.streaming_content).decode()
            events = [json.loads(line) for line in body.splitlines()]
            self.assertEqual(events[0]["type"], "start", name)
            attachment = fake.calls[-1][1]["deps"].attachment
            self.assertIsNotNone(attachment, name)
            self.assertEqual(attachment.name, name)
            self.assertEqual(attachment.suffix, Path(name).suffix.lower())
            self.assertIsInstance(attachment.upload, UploadedFile)

    def test_attachment_metadata_reaches_agent_context(self):
        self.grant_access()
        fake = FakeAgent()
        content = b"a,b,c\n1,2,3\n4,5,6"
        with patch("flare_ai.views.get_agent", return_value=fake):
            response = self.post_multipart(content=content)
            b"".join(response.streaming_content)
        attachment = fake.calls[0][1]["deps"].attachment
        self.assertEqual(attachment.name, "data.csv")
        self.assertEqual(attachment.size, len(content))
        self.assertEqual(attachment.suffix, ".csv")
        self.assertEqual(attachment.content_type, "text/csv")
        self.assertEqual(attachment.identifier, "file-1")

    def test_json_prompt_has_no_attachment_in_context(self):
        self.grant_access()
        fake = FakeAgent()
        with patch("flare_ai.views.get_agent", return_value=fake):
            response = self.post_stream()
            b"".join(response.streaming_content)
        self.assertIsNone(fake.calls[0][1]["deps"].attachment)

    def test_unsupported_extension_is_rejected_without_agent_or_usage(self):
        self.grant_access()
        fake = FakeAgent()
        with patch("flare_ai.views.get_agent", return_value=fake):
            response = self.post_multipart(name="file.pdf", content_type="application/pdf")
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
        self.assertEqual(fake.calls, [])
        self.assertFalse(UsageEvent.objects.exists())

    def test_empty_file_is_rejected_without_agent_or_usage(self):
        self.grant_access()
        fake = FakeAgent()
        with patch("flare_ai.views.get_agent", return_value=fake):
            response = self.post_multipart(content=b"")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(fake.calls, [])
        self.assertFalse(UsageEvent.objects.exists())

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=64 * 1024 * 1024)
    def test_oversized_file_is_rejected_without_agent_or_usage(self):
        self.grant_access()
        fake = FakeAgent()
        with patch("flare_ai.views.get_agent", return_value=fake):
            response = self.post_multipart(content=b"0" * (MAX_ATTACHMENT_BYTES + 1))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(fake.calls, [])
        self.assertFalse(UsageEvent.objects.exists())

    def test_oversized_request_is_rejected_before_multipart_parsing(self):
        self.grant_access()
        body = encode_multipart(BOUNDARY, {
            "conversation_id": str(uuid4()),
            "message": "What is this?",
            "page_path": "/mice/",
            "attachment": SimpleUploadedFile("data.csv", b"a,b\n1,2", content_type="text/csv"),
        })
        with (
            patch("flare_ai.views.get_agent") as get_agent,
            patch("flare_ai.views.attachment_from_files") as parse_attachment,
        ):
            response = self.client.generic(
                "POST",
                reverse("flare_ai:chat_stream"),
                data=body,
                content_type=MULTIPART_CONTENT,
                CONTENT_LENGTH=str(MAX_MULTIPART_REQUEST_BYTES + 1),
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("10 MB", response.json()["error"])
        get_agent.assert_not_called()
        parse_attachment.assert_not_called()
        self.assertFalse(UsageEvent.objects.exists())

    def test_multiple_files_are_rejected(self):
        self.grant_access()
        fake = FakeAgent()
        boundary = "flare-test-boundary"
        parts = [
            "conversation_id", "11111111-1111-1111-1111-111111111111",
            "message", "What is this?",
            "page_path", "/mice/",
            "attachment", "one.csv", "text/csv", "a,b",
            "attachment", "two.csv", "text/csv", "c,d",
        ]
        body = b""
        i = 0
        while i < len(parts):
            if parts[i] in {"attachment"}:
                field, filename, ctype, content = parts[i:i + 4]
                i += 4
                body += (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                    f"Content-Type: {ctype}\r\n\r\n"
                ).encode() + content.encode() + b"\r\n"
            else:
                field, value = parts[i], parts[i + 1]
                i += 2
                body += (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{field}"\r\n\r\n'
                ).encode() + value.encode() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        with patch("flare_ai.views.get_agent", return_value=fake):
            response = self.client.post(
                reverse("flare_ai:chat_stream"),
                data=body,
                content_type=f"multipart/form-data; boundary={boundary}",
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(fake.calls, [])
        self.assertFalse(UsageEvent.objects.exists())

    def test_greeting_cannot_carry_an_attachment(self):
        self.grant_access()
        fake_attachment = SimpleNamespace(close=lambda: None)
        with patch("flare_ai.views.attachment_from_files", return_value=fake_attachment):
            response = self.post_stream(greeting=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn("greeting", response.json()["error"].lower())

    def test_upload_is_closed_after_successful_stream(self):
        self.grant_access()
        fake = FakeAgent()
        with patch("flare_ai.views.get_agent", return_value=fake):
            response = self.post_multipart()
            b"".join(response.streaming_content)
        attachment = fake.calls[0][1]["deps"].attachment
        self.assertTrue(attachment.upload.file.closed)

    def test_upload_is_closed_after_provider_failure(self):
        self.grant_access()
        failing = FailingAgent()
        with patch("flare_ai.views.get_agent", return_value=failing):
            response = self.post_multipart()
            body = b"".join(response.streaming_content).decode()
        events = [json.loads(line) for line in body.splitlines()]
        self.assertEqual(events[-1]["type"], "error")
        attachment = failing.calls[0][1]["deps"].attachment
        self.assertTrue(attachment.upload.file.closed)

    def test_upload_is_closed_when_stream_is_interrupted(self):
        self.grant_access()
        fake = FakeAgent()
        with patch("flare_ai.views.get_agent", return_value=fake):
            response = self.post_multipart()
            events = [next(response.streaming_content) for _ in range(3)]
            # Simulate a client disconnect by closing the streaming generator.
            # response.close() also emits request_finished, which would close the
            # shared database connection used by Django's TestCase transaction.
            response._iterator.close()
        self.assertEqual([json.loads(event)["type"] for event in events], ["start", "delta", "delta"])
        attachment = fake.calls[0][1]["deps"].attachment
        self.assertTrue(attachment.upload.file.closed)


class AttachmentValidationTests(SimpleTestCase):
    def files_with(self, *uploads):
        files = MultiValueDict()
        if uploads:
            files.setlist("attachment", list(uploads))
        return files

    def make_upload(self, name="data.csv", content=b"a,b,c\n1,2,3", content_type="text/csv"):
        return SimpleUploadedFile(name, content, content_type=content_type)

    def test_no_files_returns_none(self):
        self.assertIsNone(attachment_from_files(self.files_with()))

    def test_valid_file_is_wrapped(self):
        upload = self.make_upload()
        attachment = attachment_from_files(self.files_with(upload))
        self.assertIsInstance(attachment, RequestAttachment)
        self.assertEqual(attachment.name, "data.csv")
        self.assertEqual(attachment.suffix, ".csv")
        self.assertEqual(attachment.size, len(b"a,b,c\n1,2,3"))
        self.assertEqual(attachment.content_type, "text/csv")
        self.assertEqual(attachment.identifier, "file-1")
        self.assertIs(attachment.upload, upload)

    def test_unsupported_extension_is_rejected(self):
        upload = self.make_upload(name="archive.zip", content_type="application/zip")
        with self.assertRaises(ValidationError):
            attachment_from_files(self.files_with(upload))

    def test_extension_only_is_not_enough_for_unsupported_file(self):
        upload = self.make_upload(name="archive.bin", content_type="application/octet-stream")
        with self.assertRaises(ValidationError):
            attachment_from_files(self.files_with(upload))

    def test_empty_file_is_rejected(self):
        upload = self.make_upload(content=b"")
        with self.assertRaises(ValidationError):
            attachment_from_files(self.files_with(upload))

    def test_oversized_file_is_rejected(self):
        upload = self.make_upload(content=b"0" * (MAX_ATTACHMENT_BYTES + 1))
        with self.assertRaises(ValidationError):
            attachment_from_files(self.files_with(upload))

    def test_multiple_files_are_rejected(self):
        uploads = [
            self.make_upload(name="one.csv", content_type="text/csv"),
            self.make_upload(name="two.txt", content_type="text/plain"),
        ]
        with self.assertRaises(ValidationError):
            attachment_from_files(self.files_with(*uploads))

    def test_streaming_handler_stops_before_file_exceeds_limit(self):
        request = SimpleNamespace()
        handler = AttachmentSizeUploadHandler(request)
        final_chunk = b"x" * 2
        with self.assertRaises(StopUpload):
            handler.receive_data_chunk(final_chunk, MAX_ATTACHMENT_BYTES - 1)
        self.assertTrue(attachment_upload_was_too_large(request))

    def test_streaming_handler_passes_chunks_within_limit(self):
        request = SimpleNamespace()
        handler = AttachmentSizeUploadHandler(request)
        chunk = b"safe"
        self.assertIs(handler.receive_data_chunk(chunk, MAX_ATTACHMENT_BYTES - len(chunk)), chunk)
        self.assertFalse(attachment_upload_was_too_large(request))

    def test_install_limit_skips_when_upload_handlers_locked(self):
        """Simulates CsrfViewMiddleware having already read request.POST.

        When upload_handlers are locked, insert() raises AttributeError.
        install_attachment_upload_limit must not propagate that error.
        """
        from flare_ai.attachments import install_attachment_upload_limit

        class LockedHandlers:
            def insert(self, index, value):
                raise AttributeError("You cannot alter upload handlers after the upload has been processed.")

        request = SimpleNamespace(
            META={"CONTENT_LENGTH": str(1024)},  # well under limit
            upload_handlers=LockedHandlers(),
        )
        # Should not raise
        install_attachment_upload_limit(request)

    def test_install_limit_rejects_oversized_content_length(self):
        """The CONTENT_LENGTH check fires before any handler insertion."""
        from flare_ai.attachments import install_attachment_upload_limit

        request = SimpleNamespace(
            META={"CONTENT_LENGTH": str(MAX_MULTIPART_REQUEST_BYTES + 1)},
            upload_handlers=[],
        )
        with self.assertRaises(ValidationError):
            install_attachment_upload_limit(request)


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
