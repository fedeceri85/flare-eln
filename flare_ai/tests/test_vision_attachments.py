"""Tests for vision attachment support (images and PDFs)."""

import io
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils.datastructures import MultiValueDict

from flare_ai.attachments import (
    ALLOWED_ATTACHMENT_SUFFIXES,
    IMAGE_SUFFIXES,
    MAX_PDF_PAGES,
    PDF_SUFFIXES,
    RequestAttachment,
    attachment_from_files,
)


def _make_upload(name, content, content_type="application/octet-stream"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def _make_files(upload):
    files = MultiValueDict()
    files.setlist("attachment", [upload])
    return files


class SuffixAcceptanceTests(SimpleTestCase):
    """Verify that allowed image/PDF suffixes are accepted and others are not."""

    def test_all_image_suffixes_are_allowed(self):
        for suffix in IMAGE_SUFFIXES:
            self.assertIn(suffix, ALLOWED_ATTACHMENT_SUFFIXES)

    def test_pdf_suffix_is_allowed(self):
        self.assertIn(".pdf", ALLOWED_ATTACHMENT_SUFFIXES)

    def test_disallowed_suffix_is_rejected(self):
        upload = _make_upload(name="archive.zip", content=b"zipdata", content_type="application/zip")
        with self.assertRaises(ValidationError):
            attachment_from_files(_make_files(upload))

    def test_uppercase_extension_is_accepted(self):
        upload = _make_upload(name="photo.PNG", content=b"\x89PNG fake data", content_type="image/png")
        attachment = attachment_from_files(_make_files(upload))
        self.assertEqual(attachment.suffix, ".png")

    def test_image_attachment_has_correct_suffix(self):
        for name, expected in [("a.png", ".png"), ("b.jpg", ".jpg"), ("c.jpeg", ".jpeg"), ("d.webp", ".webp")]:
            upload = _make_upload(name=name, content=b"imgdata", content_type="image/x")
            attachment = attachment_from_files(_make_files(upload))
            self.assertEqual(attachment.suffix, expected)


class ImageDataURLTests(SimpleTestCase):
    """Test image_data_urls() for image files."""

    def _make_attachment(self, name="test.png", content=b"\x89PNG fake data", suffix=".png"):
        upload = SimpleUploadedFile(name, content, content_type="image/png")
        return RequestAttachment(
            identifier="file-1",
            name=name,
            suffix=suffix,
            size=len(content),
            content_type="image/png",
            upload=upload,
        )

    def test_png_returns_single_data_url(self):
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        attachment = self._make_attachment(content=fake_png)
        urls = attachment.image_data_urls()
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith("data:image/png;base64,"))

    def test_jpeg_returns_single_data_url(self):
        fake_jpg = b"\xff\xd8\xff" + b"\x00" * 50 + b"\xff\xd9"
        attachment = self._make_attachment(name="photo.jpg", content=fake_jpg, suffix=".jpg")
        urls = attachment.image_data_urls()
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith("data:image/jpeg;base64,"))

    def test_csv_returns_empty_list(self):
        attachment = self._make_attachment(name="data.csv", content=b"a,b\n1,2", suffix=".csv")
        self.assertEqual(attachment.image_data_urls(), [])

    def test_txt_returns_empty_list(self):
        attachment = self._make_attachment(name="notes.txt", content=b"hello", suffix=".txt")
        self.assertEqual(attachment.image_data_urls(), [])


class PDFDataURLTests(SimpleTestCase):
    """Test image_data_urls() for PDF files using PyMuPDF."""

    def _make_pdf(self, page_count=1):
        """Create a minimal valid PDF with the given page count."""
        import pymupdf
        doc = pymupdf.open()
        for _ in range(page_count):
            page = doc.new_page()
            page.insert_text((72, 72), "Test page content for transcription.")
        data = doc.tobytes()
        doc.close()
        return data

    def _make_pdf_attachment(self, page_count=1):
        data = self._make_pdf(page_count)
        upload = SimpleUploadedFile("test.pdf", data, content_type="application/pdf")
        return RequestAttachment(
            identifier="file-1",
            name="test.pdf",
            suffix=".pdf",
            size=len(data),
            content_type="application/pdf",
            upload=upload,
        )

    def test_single_page_pdf_returns_one_data_url(self):
        attachment = self._make_pdf_attachment(page_count=1)
        urls = attachment.image_data_urls()
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith("data:image/png;base64,"))

    def test_three_page_pdf_returns_three_data_urls(self):
        attachment = self._make_pdf_attachment(page_count=3)
        urls = attachment.image_data_urls()
        self.assertEqual(len(urls), 3)
        for url in urls:
            self.assertTrue(url.startswith("data:image/png;base64,"))

    def test_max_pages_pdf_is_accepted(self):
        attachment = self._make_pdf_attachment(page_count=MAX_PDF_PAGES)
        urls = attachment.image_data_urls()
        self.assertEqual(len(urls), MAX_PDF_PAGES)

    def test_16_page_pdf_exceeds_limit(self):
        attachment = self._make_pdf_attachment(page_count=16)
        with self.assertRaises(ValidationError) as ctx:
            attachment.image_data_urls()
        self.assertIn("16", str(ctx.exception))
        self.assertIn(str(MAX_PDF_PAGES), str(ctx.exception))

    def test_corrupted_pdf_raises_validation_error(self):
        upload = SimpleUploadedFile("corrupt.pdf", b"this is not a valid PDF at all", content_type="application/pdf")
        attachment = RequestAttachment(
            identifier="file-1",
            name="corrupt.pdf",
            suffix=".pdf",
            size=len(b"this is not a valid PDF at all"),
            content_type="application/pdf",
            upload=upload,
        )
        with self.assertRaises(ValidationError):
            attachment.image_data_urls()

    def test_pdf_instruction_mentions_transcription(self):
        attachment = self._make_pdf_attachment()
        instruction = attachment.instruction()
        self.assertIn("transcribe", instruction.lower())
        self.assertNotIn("No file-reading tool", instruction)

    def test_csv_instruction_mentions_read_tool(self):
        upload = SimpleUploadedFile("data.csv", b"a,b\n1,2", content_type="text/csv")
        attachment = RequestAttachment(
            identifier="file-1",
            name="data.csv",
            suffix=".csv",
            size=len(b"a,b\n1,2"),
            content_type="text/csv",
            upload=upload,
        )
        instruction = attachment.instruction()
        self.assertIn("read_attachment", instruction)
        self.assertNotIn("No file-reading tool", instruction)


@override_settings(
    ROOT_URLCONF="flare_ai.tests.urls",
    FLARE_AI_ENABLED=True,
    FLARE_AI_GROUP_NAME="FLARE AI test users",
)
class ChatStreamVisionTests(TestCase):
    """Test that chat_stream builds multi-modal prompts for image/PDF attachments."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="ai-vision-user", password="test-password"
        )
        cls.ai_group = Group.objects.create(name="FLARE AI test users")

    def setUp(self):
        self.client.force_login(self.user)
        self.user.groups.add(self.ai_group)

    def _fake_agent(self):
        captured = {}

        class FakeStreamResult:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def stream_text(self, *, delta=False):
                yield "Transcription: hello"
            def new_messages_json(self):
                return b"[]"

        class FakeAgent:
            def run_stream_sync(self, user_prompt, **kwargs):
                captured["prompt"] = user_prompt
                return FakeStreamResult()

        return FakeAgent(), captured

    def test_image_attachment_produces_multimodal_prompt(self):
        fake_agent, captured = self._fake_agent()
        png_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        upload = SimpleUploadedFile("lab_note.png", png_content, content_type="image/png")
        with patch("flare_ai.views.get_agent", return_value=fake_agent):
            response = self.client.post(
                "/ai/chat/stream/",
                data={
                    "conversation_id": str(uuid4()),
                    "message": "Transcribe this.",
                    "page_path": "/",
                    "attachment": upload,
                },
            )
            self.assertEqual(response.status_code, 200)
            # Consume the streaming response to trigger the generator
            body = b"".join(response.streaming_content).decode()
        prompt = captured["prompt"]
        self.assertIsInstance(prompt, list)
        self.assertEqual(prompt[0], "Transcribe this.")
        from pydantic_ai.messages import ImageUrl
        self.assertIsInstance(prompt[1], ImageUrl)
        self.assertTrue(prompt[1].url.startswith("data:image/png;base64,"))

    def test_multipart_without_attachment_stays_string(self):
        """Regression: multipart POST with no file should still send a plain string."""
        fake_agent, captured = self._fake_agent()
        with patch("flare_ai.views.get_agent", return_value=fake_agent):
            response = self.client.post(
                "/ai/chat/stream/",
                data={
                    "conversation_id": str(uuid4()),
                    "message": "Hello",
                    "page_path": "/",
                },
            )
            self.assertEqual(response.status_code, 200)
            # Consume the streaming response to trigger the generator
            body = b"".join(response.streaming_content).decode()
        self.assertIsInstance(captured["prompt"], str)
        self.assertEqual(captured["prompt"], "Hello")