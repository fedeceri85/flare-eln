from io import BytesIO, StringIO
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from ..models import Experiment, Project, Recording, RecordingImage, RecordingType
from ..recording_images import (
    compress_image, document_path, image_path, save_documents, save_images,
)


def upload(name="sample.png", size=(400, 200), mode="RGB", **save_options):
    output = BytesIO()
    Image.new(mode, size, (30, 90, 160, 120) if mode == "RGBA" else (30, 90, 160)).save(
        output, format="JPEG" if name.endswith(".jpg") else "PNG", **save_options,
    )
    return SimpleUploadedFile(name, output.getvalue())


def pdf_upload(name="protocol.pdf", data=b"%PDF-1.7\nrecording document\n%%EOF"):
    return SimpleUploadedFile(name, data, content_type="application/pdf")


@override_settings(ALLOWED_HOSTS=["testserver"])
class RecordingImageTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.settings_override = override_settings(RECORDING_MEDIA_ROOT=self.root / "default", RECORDING_MEDIA_BOUNDARY="")
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.owner = get_user_model().objects.create_user(username="image-owner")
        self.viewer = get_user_model().objects.create_user(username="image-viewer")
        self.admin = get_user_model().objects.create_user(username="image-admin", is_staff=True)
        self.project = Project.objects.create(name="Image project")
        self.kind = RecordingType.objects.get(slug="imaging")
        self.experiment = Experiment.objects.create(project=self.project, recording_type=self.kind, name="Image experiment")
        self.recording = Recording.objects.create(owner=self.owner, experiment=self.experiment, recording_type=self.kind, recording_date="2026-10-25")
        self.url = reverse("lab:recording_images", args=[self.project.pk, self.experiment.pk, self.recording.pk])
        self.list_url = reverse("lab:recordings_page", args=[self.project.pk, self.experiment.pk])
        self.client.force_login(self.owner)

    def add_image(self, **kwargs):
        save_images(self.recording, [upload(**kwargs)])
        return self.recording.images.latest("created_at")

    def gallery(self):
        return self.client.get(self.url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    def test_table_counts_registered_attachments_without_touching_files(self):
        image = self.add_image()
        with patch("lab.recording_images.image_available", side_effect=AssertionError("Table checked image files")), patch("lab.recording_images.image_path", side_effect=AssertionError("Table resolved a file path")):
            response = self.client.get(self.list_url)
        self.assertContains(response, "Images (1)")
        image_path(image).unlink()
        payload = self.gallery().json()
        self.assertEqual(payload["registered_count"], 1)
        self.assertEqual(payload["unavailable_count"], 1)
        self.assertEqual(payload["images"], [])
        response = self.client.get(self.url)
        self.assertContains(response, "currently unavailable")

    def test_attachment_audit_reports_without_modifying_files_or_records(self):
        healthy = self.add_image()
        missing = self.add_image(name="missing.png")
        image_path(missing).unlink()
        before = image_path(healthy).read_bytes()
        output = StringIO()
        call_command("audit_recording_images", stdout=output)
        self.assertIn("Checked 2 registered attachments; 1 unavailable", output.getvalue())
        self.assertEqual(RecordingImage.objects.count(), 2)
        self.assertEqual(image_path(healthy).read_bytes(), before)
        self.assertFalse(image_path(missing).exists())

    def test_default_upload_compression_download(self):
        response = self.client.post(self.url, {"action": "upload", "images": [upload(size=(3000, 1500))]}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["images"]), 1)
        self.assertNotIn("caption", payload["images"][0])
        image = self.recording.images.get()
        self.assertEqual((image.width, image.height), (2048, 1024))
        self.assertEqual(Path(image.storage_location.directory), self.root / "default")
        self.assertTrue(image.filename.endswith(".webp"))
        self.assertLess(image.size_bytes, 100_000)
        response = self.client.get(payload["images"][0]["download_url"])
        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertTrue(response["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(response["Cache-Control"], "private, no-store")
        with Image.open(BytesIO(response.content)) as saved:
            self.assertEqual(saved.format, "WEBP")
            self.assertEqual(saved.size, (2048, 1024))

    def test_small_images_are_not_enlarged_and_transparency_is_kept(self):
        image = self.add_image(size=(100, 50), mode="RGBA")
        with Image.open(image_path(image)) as saved:
            self.assertEqual(saved.size, (100, 50))
            self.assertEqual(saved.mode, "RGBA")

    def test_tiff_uploads_compress_first_page_to_webp(self):
        for extension in ("tif", "tiff"):
            with self.subTest(extension=extension):
                data = BytesIO()
                Image.new("RGB", (120, 80), "red").save(data, format="TIFF", save_all=True,
                    append_images=[Image.new("RGB", (40, 60), "blue")])
                response = self.client.post(self.url, {"action": "upload", "images":
                    SimpleUploadedFile("gel." + extension, data.getvalue(), content_type="image/tiff")},
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest")
                self.assertEqual(response.status_code, 200)
                image = self.recording.images.get(original_name="gel." + extension)
                with Image.open(image_path(image)) as result:
                    self.assertEqual(result.format, "WEBP")
                    self.assertEqual(result.size, (120, 80))
                    self.assertGreater(result.getpixel((0, 0))[0], 200)

    def test_jpeg_orientation_is_applied_before_saving(self):
        exif = Image.Exif()
        exif[274] = 6
        data, width, height = compress_image(upload(name="oriented.jpg", exif=exif))
        self.assertEqual((width, height), (200, 400))
        with Image.open(BytesIO(data)) as saved:
            self.assertFalse(saved.getexif())

    def test_missing_image_is_omitted_then_reappears_without_reupload(self):
        image = self.add_image()
        path = image_path(image)
        data = path.read_bytes()
        path.unlink()
        self.assertEqual(self.gallery().json()["images"], [])
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Images (1)")
        self.assertEqual(self.gallery().json()["unavailable_count"], 1)
        self.assertEqual(self.client.get(reverse("lab:recording_image_file", args=[image.pk])).status_code, 404)
        self.assertTrue(RecordingImage.objects.filter(pk=image.pk).exists())
        path.write_bytes(data)
        self.assertEqual(len(self.gallery().json()["images"]), 1)
        self.assertContains(self.client.get(self.list_url), "Images (1)")

    def test_missing_folder_and_permission_errors_never_break_recording_pages(self):
        image = self.add_image()
        location = image_path(image).parent
        hidden = location.with_name("offline")
        location.rename(hidden)
        self.assertEqual(self.gallery().json()["images"], [])
        self.assertEqual(self.client.get(self.list_url).status_code, 200)
        hidden.rename(location)
        with patch("lab.recording_images.image_path", side_effect=PermissionError):
            self.assertEqual(self.gallery().json()["images"], [])
            self.assertEqual(self.client.get(self.list_url).status_code, 200)
            self.assertEqual(self.client.get(reverse("lab:recording_image_file", args=[image.pk])).status_code, 404)

    def test_late_file_read_error_returns_404_instead_of_streaming_failure(self):
        image = self.add_image()
        with patch("lab.recording_images.image_path") as path:
            path.return_value.open.return_value.__enter__.return_value.read.side_effect = OSError("NAS offline")
            self.assertEqual(self.client.get(reverse("lab:recording_image_file", args=[image.pk])).status_code, 404)

    def test_failed_storage_upload_does_not_change_recording_or_existing_images(self):
        self.add_image()
        with patch("lab.recording_images.validate_directory", side_effect=ValidationError("Unavailable")):
            response = self.client.post(self.url, {"action": "upload", "images": upload()}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.recording.images.count(), 1)
        self.assertEqual(len(response.json()["images"]), 1)

    def test_deployment_media_root_cannot_escape_persistent_media_mount(self):
        mounted = self.root / "media"
        mounted.mkdir()
        with override_settings(
            RECORDING_MEDIA_ROOT=self.root,
            RECORDING_MEDIA_BOUNDARY=str(mounted),
        ):
            with self.assertRaises(ValidationError):
                save_images(self.recording, [upload()])
            link = mounted / "outside"
            link.symlink_to(self.root, target_is_directory=True)
        with override_settings(
            RECORDING_MEDIA_ROOT=link,
            RECORDING_MEDIA_BOUNDARY=str(mounted),
        ):
            with self.assertRaises(ValidationError):
                save_images(self.recording, [upload()])
        with override_settings(
            RECORDING_MEDIA_ROOT=mounted,
            RECORDING_MEDIA_BOUNDARY=str(mounted),
        ):
            save_images(self.recording, [upload()])
        self.assertEqual(
            Path(self.recording.images.get().storage_location.directory),
            mounted,
        )

    def test_private_viewing_and_owner_staff_modification_permissions(self):
        image = self.add_image()
        file_url = reverse("lab:recording_image_file", args=[image.pk])
        self.client.logout()
        for url in (file_url, self.url):
            self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(file_url).status_code, 200)
        self.assertFalse(self.gallery().json()["can_modify"])
        for action in ("upload", "remove"):
            self.assertEqual(self.client.post(self.url, {"action": action, "image_id": image.pk}).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.post(self.url, {"action": "remove", "image_id": image.pk}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.json()["images"], [])
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(self.url, {"action": "remove", "image_id": image.pk}).status_code, 403)

    def test_invalid_upload_batch_creates_no_files_or_attachments(self):
        response = self.client.post(self.url, {"action": "upload", "images": [upload(), SimpleUploadedFile("bad.png", b"not an image")]}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.recording.images.exists())
        self.assertFalse(list(self.root.rglob("*.webp")))
        with self.assertRaises(ValidationError):
            save_images(self.recording, [upload()] * 11)
        oversized = upload()
        oversized.size = 21 * 1024 * 1024
        with self.assertRaises(ValidationError):
            compress_image(oversized)
        gif = BytesIO()
        Image.new("RGB", (10, 10)).save(gif, "GIF")
        with self.assertRaises(ValidationError):
            compress_image(SimpleUploadedFile("image.png", gif.getvalue()))

    def test_database_failure_cleans_up_written_files(self):
        create = RecordingImage.objects.create
        calls = []

        def fail_second_create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise IntegrityError("failed")
            return create(**kwargs)

        with patch("lab.recording_images.RecordingImage.objects.create", side_effect=fail_second_create):
            with self.assertRaises(IntegrityError):
                save_images(self.recording, [upload(), upload(name="second.png")])
        self.assertFalse(list(self.root.rglob("*.webp")))
        self.assertFalse(self.recording.images.exists())

    def test_image_counts_do_not_add_database_queries_per_attachment(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.add_image()
        with CaptureQueriesContext(connection) as one:
            self.client.get(self.list_url)
        for _ in range(4):
            self.add_image()
        with CaptureQueriesContext(connection) as several:
            response = self.client.get(self.list_url)
        self.assertEqual(len(one), len(several))
        self.assertContains(response, "Images (5)")

    def test_delete_removes_files_only_after_commit_and_tolerates_missing_files(self):
        image = self.add_image()
        path = image_path(image)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, {"action": "remove", "image_id": image.pk})
            self.assertTrue(path.exists())
        self.assertFalse(path.exists())
        missing = self.add_image()
        image_path(missing).unlink()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, {"action": "remove", "image_id": missing.pk})
        self.assertEqual(response.status_code, 302)

    def test_recording_delete_cascades_file_cleanup_and_rollback_keeps_files(self):
        from django.db import transaction
        image = self.add_image()
        path = image_path(image)
        try:
            with transaction.atomic():
                Recording.objects.get(pk=self.recording.pk).delete()
                raise ValueError("rollback")
        except ValueError:
            pass
        self.assertTrue(path.exists())
        self.assertTrue(RecordingImage.objects.filter(pk=image.pk).exists())
        with self.captureOnCommitCallbacks(execute=True):
            self.recording.delete()
        self.assertFalse(path.exists())

    def test_image_removal_is_scoped_to_the_recording(self):
        image = self.add_image()
        other = Recording.objects.create(owner=self.owner, experiment=self.experiment, recording_type=self.kind, recording_date="2026-10-24")
        other_url = reverse("lab:recording_images", args=[self.project.pk, self.experiment.pk, other.pk])
        self.assertEqual(self.client.post(other_url, {"action": "remove", "image_id": image.pk}).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"action": "remove", "image_id": "invalid"}).status_code, 404)

    def test_new_recording_does_not_inherit_existing_images(self):
        from ..forms import RecordingForm
        self.add_image()
        form = RecordingForm({"recording_date": "2026-10-25", "mice": ""}, experiment=self.experiment, owner=self.owner)
        self.assertTrue(form.is_valid(), form.errors)
        new_recording = form.save()
        self.assertFalse(new_recording.images.exists())

    def test_manual_add_shows_media_box_and_saves_selected_images_with_recording(self):
        url = reverse("lab:add_recording", args=[self.project.pk, self.experiment.pk])
        response = self.client.get(url)
        self.assertContains(response, '<h2>Media</h2>', html=True)
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'name="media-images"')
        result = self.client.post(url, {"recording_date": "2026-10-26", "mice": "",
            "media-images": [upload(), upload(name="second.png")]})
        self.assertEqual(result.status_code, 302)
        created = self.experiment.recordings.get(recording_date="2026-10-26")
        self.assertEqual(created.images.count(), 2)
        self.assertNotContains(response, "media-caption")

    def test_manual_add_invalid_images_or_storage_do_not_leave_a_partial_recording(self):
        url = reverse("lab:add_recording", args=[self.project.pk, self.experiment.pk])
        response = self.client.post(url, {"recording_date": "2026-10-26", "mice": "",
            "media-images": SimpleUploadedFile("invalid.png", b"not an image")})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.experiment.recordings.filter(recording_date="2026-10-26").exists())
        self.assertFalse(list(self.root.rglob("*.webp")))
        with patch("lab.recording_images.validate_directory", side_effect=ValidationError("Unavailable")):
            response = self.client.post(url, {"recording_date": "2026-10-26", "mice": "", "media-images": upload()})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.experiment.recordings.filter(recording_date="2026-10-26").exists())

    def test_pdf_upload_payload_table_icon_and_private_download(self):
        response = self.client.post(
            self.url, {"action": "upload_documents", "documents": [pdf_upload()]},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["document_registered_count"], 1)
        self.assertEqual(payload["documents"][0]["name"], "protocol.pdf")
        document = self.recording.documents.get()
        self.assertEqual(Path(document.storage_location.directory), self.root / "default")
        self.assertEqual(document_path(document).read_bytes(), b"%PDF-1.7\nrecording document\n%%EOF")
        table = self.client.get(self.list_url)
        self.assertContains(table, 'data-gallery-tab="documents"')
        self.assertContains(table, "PDF documents (1)")
        download = self.client.get(payload["documents"][0]["download_url"])
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/pdf")
        self.assertTrue(download["Content-Disposition"].startswith("attachment;"))
        self.client.logout()
        self.assertEqual(self.client.get(payload["documents"][0]["download_url"]).status_code, 302)

    def test_invalid_pdf_batches_write_nothing_and_enforce_limits(self):
        response = self.client.post(
            self.url,
            {"action": "upload_documents", "documents": [pdf_upload(), pdf_upload("bad.pdf", b"not pdf")]},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.recording.documents.exists())
        self.assertFalse(list(self.root.rglob("*.pdf")))
        with self.assertRaises(ValidationError):
            save_documents(self.recording, [pdf_upload(str(index) + ".pdf") for index in range(11)])
        oversized = pdf_upload()
        oversized.size = 10 * 1024 * 1024 + 1
        with self.assertRaises(ValidationError):
            save_documents(self.recording, [oversized])

    def test_pdf_permissions_missing_file_and_scoped_removal(self):
        document = save_documents(self.recording, [pdf_upload()])[0]
        download_url = reverse("lab:recording_document_file", args=[document.pk])
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(download_url).status_code, 200)
        self.assertEqual(self.client.post(self.url, {"action": "remove_document", "document_id": document.pk}).status_code, 403)
        self.client.force_login(self.owner)
        path = document_path(document)
        path.unlink()
        payload = self.gallery().json()
        self.assertEqual(payload["document_unavailable_count"], 1)
        self.assertEqual(payload["documents"], [])
        self.assertEqual(self.client.get(download_url).status_code, 404)
        other = Recording.objects.create(owner=self.owner, experiment=self.experiment, recording_type=self.kind, recording_date="2026-10-24")
        other_url = reverse("lab:recording_images", args=[self.project.pk, self.experiment.pk, other.pk])
        self.assertEqual(self.client.post(other_url, {"action": "remove_document", "document_id": document.pk}).status_code, 404)

    def test_manual_add_saves_images_and_documents_atomically(self):
        url = reverse("lab:add_recording", args=[self.project.pk, self.experiment.pk])
        result = self.client.post(url, {
            "recording_date": "2026-10-27", "mice": "", "media-images": upload(),
            "media-documents": [pdf_upload()],
        })
        self.assertEqual(result.status_code, 302)
        created = self.experiment.recordings.get(recording_date="2026-10-27")
        self.assertEqual(created.images.count(), 1)
        self.assertEqual(created.documents.count(), 1)
        response = self.client.post(url, {
            "recording_date": "2026-10-28", "mice": "", "media-images": upload(),
            "media-documents": pdf_upload("bad.pdf", b"invalid"),
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.experiment.recordings.filter(recording_date="2026-10-28").exists())
