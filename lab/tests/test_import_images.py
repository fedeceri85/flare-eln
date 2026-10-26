from datetime import timedelta
from io import BytesIO, StringIO
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from ..models import Experiment, Mouse, Project, Protocol, RecordingImage, RecordingType, SpreadsheetImportDraft
from ..recording_images import activate_directory, image_path, save_import_images
from ..spreadsheet_import.engine import animal_groups, build_review, commit_import, initial_resolution
from ..spreadsheet_import.mapping import new_definition
from ..spreadsheet_import.source import read_upload, table_from_sheet
from ..test_navigation import Markup
from ._support import create_test_crossing


def image_upload(name="gel.png"):
    output = BytesIO()
    Image.new("RGB", (400, 200), "gray").save(output, "PNG")
    return SimpleUploadedFile(name, output.getvalue())


@override_settings(ALLOWED_HOSTS=["testserver"])
class ImportSharedImageTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        configuration = override_settings(RECORDING_MEDIA_ROOT=self.root, RECORDING_MEDIA_BOUNDARY="")
        configuration.enable()
        self.addCleanup(configuration.disable)
        self.user = get_user_model().objects.create_user(username="shared-image-importer")
        self.other = get_user_model().objects.create_user(username="other-image-importer")
        self.client.force_login(self.user)
        self.protocol = Protocol.objects.create(protocol_number=771, licence_reference="IMAGES", name="Image import")
        crossing, _ = create_test_crossing(("Shared image line",))
        self.mouse = Mouse.objects.create(mouse_id="IMAGE-1", protocol=self.protocol, crossing_definition=crossing)
        self.kind = RecordingType.objects.create(name="Image import", slug="image-import")
        self.project = Project.objects.create(name="Shared image project")
        self.experiment = Experiment.objects.create(project=self.project, name="Shared image experiment", recording_type=self.kind)

    def draft(self, text="Date,Mouse ID\n25.10.26,IMAGE-1\n25.10.26,IMAGE-1\n26.10.26,IMAGE-1\n"):
        source = read_upload(SimpleUploadedFile("records.csv", text.encode()))
        table = table_from_sheet(source[0], 1)
        draft = SpreadsheetImportDraft.objects.create(owner=self.user, experiment=self.experiment, filename="records.csv",
            source=source, configuration=new_definition(self.experiment, table["columns"]),
            state={"sheet": source[0]["name"], "header_row": 1, "selected_rows": [row["number"] for row in table["rows"]]})
        draft.state["animals"] = {group["key"]: initial_resolution(group, draft.configuration) for group in animal_groups(draft)}
        draft.save()
        return draft

    def url(self, draft):
        return reverse("lab:import_recordings_draft", args=[self.project.pk, self.experiment.pk, draft.pk])

    def images_url(self, draft):
        return reverse("lab:import_shared_images", args=[self.project.pk, self.experiment.pk, draft.pk])

    def stage(self, draft, *uploads):
        response = self.client.get(self.url(draft), {"step": "setup"})
        result = self.client.post(self.images_url(draft), {"revision": response.context["revision"],
            "image_action": "upload", "images": list(uploads) or [image_upload()]})
        self.assertEqual(result.status_code, 302)
        draft.refresh_from_db()
        return result

    def review(self, draft):
        draft.refresh_from_db()
        preview = build_review(draft)
        self.assertEqual(preview["errors"], [])
        draft.state["review_fingerprint"] = preview["fingerprint"]
        draft.save()
        return preview

    def test_media_is_in_settings_with_separate_upload_form_and_no_extra_tab(self):
        draft = self.draft()
        response = self.client.get(self.url(draft), {"step": "setup"})
        self.assertContains(response, "<h2>Media</h2>", html=True)
        self.assertNotIn("images", [tab["key"] for tab in response.context["tabs"]])
        markup = Markup(response.content.decode())
        upload_form = markup.find("form", enctype="multipart/form-data")[0]
        self.assertFalse(upload_form.within("form"))
        field = markup.find("input", name="images")[0]
        self.assertEqual(field.attrs["form"], upload_form.attrs["id"])
        result = self.client.post(self.url(draft), {"step": "setup", "revision": response.context["revision"], "next_step": "mapping", "date_order": "DMY", "grouping": "rows"})
        self.assertRedirects(result, self.url(draft) + "?step=mapping")
        self.review(draft)
        result = commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(result["shared_images"], 0)
        self.assertEqual(result["recordings"], 3)

    def test_shared_files_attach_to_every_date_crossing_group_and_survive_draft_cleanup(self):
        other_crossing, _ = create_test_crossing(("Another shared image line",))
        Mouse.objects.create(mouse_id="IMAGE-2", protocol=self.protocol, crossing_definition=other_crossing)
        draft = self.draft("Date,Mouse ID\n25.10.26,IMAGE-1\n25.10.26,IMAGE-2\n26.10.26,IMAGE-1\n")
        draft.configuration["grouping"] = "date"
        draft.save()
        self.stage(draft, image_upload(), image_upload("other.png"))
        self.assertEqual(draft.shared_images.count(), 2)
        self.assertEqual(self.experiment.recordings.count(), 0)
        preview = self.review(draft)
        self.assertEqual(len(preview["shared_images"]), 2)
        self.assertEqual(len(preview["records"]), 3)
        with self.captureOnCommitCallbacks(execute=True):
            result = commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(result["image_attachments"], 6)
        self.assertEqual(result["shared_images"], 2)
        self.assertEqual(len(list(self.root.glob("*.webp"))), 2)
        self.assertEqual(draft.shared_images.count(), 0)
        for recording in self.experiment.recordings.all():
            self.assertEqual(recording.images.count(), 2)
        self.assertEqual(commit_import(draft.pk, self.user, self.experiment, []), result)
        self.assertEqual(RecordingImage.objects.count(), 6)
        with self.captureOnCommitCallbacks(execute=True):
            draft.delete()
        self.assertEqual(len(list(self.root.glob("*.webp"))), 2)

    def test_removing_one_attachment_preserves_other_recordings_until_the_last_link(self):
        draft = self.draft()
        self.stage(draft)
        self.review(draft)
        with self.captureOnCommitCallbacks(execute=True):
            commit_import(draft.pk, self.user, self.experiment, [])
        images = list(RecordingImage.objects.select_related("storage_location"))
        path = image_path(images[0])
        self.assertEqual(len(images), 3)
        with self.captureOnCommitCallbacks(execute=True):
            images[0].delete()
        self.assertTrue(path.exists())
        self.assertNotEqual(images[1].pk, images[2].pk)
        with self.captureOnCommitCallbacks(execute=True):
            images[1].recording.delete()
        self.assertTrue(path.exists())
        with self.captureOnCommitCallbacks(execute=True):
            images[2].delete()
        self.assertFalse(path.exists())

    def test_staged_preview_is_private_and_expiry_cleanup_removes_unused_files(self):
        draft = self.draft()
        self.stage(draft)
        image = draft.shared_images.select_related("storage_location").get()
        url = reverse("lab:import_image_file", args=[image.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(self.images_url(draft), {"image_action": "remove", "image_id": image.pk}).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        draft.expires_at = timezone.now() - timedelta(seconds=1)
        draft.save()
        path = image_path(image)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 404)
        with self.captureOnCommitCallbacks(execute=True):
            call_command("cleanup_import_drafts", stdout=StringIO())
        self.assertFalse(path.exists())

    def test_stale_tab_cannot_upload_and_new_upload_invalidates_review(self):
        draft = self.draft()
        old = self.client.get(self.url(draft), {"step": "setup"}).context["revision"]
        self.stage(draft)
        response = self.client.post(self.images_url(draft), {"revision": old, "image_action": "upload", "images": image_upload()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(draft.shared_images.count(), 1)
        self.review(draft)
        self.stage(draft, image_upload("another.png"))
        self.assertNotIn("review_fingerprint", draft.state)
        with self.assertRaisesMessage(ValidationError, "changed after review"):
            commit_import(draft.pk, self.user, self.experiment, [])

    def test_failed_import_keeps_staged_files_and_retry_attaches_once(self):
        draft = self.draft()
        self.stage(draft)
        self.review(draft)
        image = draft.shared_images.select_related("storage_location").get()
        with patch("lab.forms.recordings.RecordingForm.save", side_effect=ValidationError("failed")):
            with self.assertRaises(ValidationError):
                commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(self.experiment.recordings.count(), 0)
        self.assertEqual(draft.shared_images.count(), 1)
        self.assertTrue(image_path(image).exists())
        result = commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(result["image_attachments"], 3)

    def test_upload_transaction_failure_cleans_up_new_staging_files(self):
        draft = self.draft()
        response = self.client.get(self.url(draft), {"step": "setup"})
        with patch("lab.views.spreadsheet_import.SpreadsheetImportDraft.save", side_effect=IntegrityError("failed")):
            result = self.client.post(self.images_url(draft), {"revision": response.context["revision"], "image_action": "upload", "images": image_upload()})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(draft.shared_images.count(), 0)
        self.assertFalse(list(self.root.glob("*.webp")))

    def test_unavailable_files_are_omitted_and_review_must_refresh_if_availability_changes(self):
        draft = self.draft()
        self.stage(draft)
        self.review(draft)
        image = draft.shared_images.select_related("storage_location").get()
        path = image_path(image)
        original = path.read_bytes()
        path.unlink()
        with self.assertRaisesMessage(ValidationError, "changed after review"):
            commit_import(draft.pk, self.user, self.experiment, [])
        preview = self.review(draft)
        self.assertEqual(preview["shared_images"], [])
        self.assertEqual(preview["unavailable_images"], 1)
        path.write_bytes(original)
        preview = self.review(draft)
        self.assertEqual(len(preview["shared_images"]), 1)

    def test_missing_storage_can_be_skipped_after_review_without_breaking_import(self):
        draft = self.draft()
        self.stage(draft)
        image = draft.shared_images.select_related("storage_location").get()
        image_path(image).unlink()
        self.review(draft)
        with self.captureOnCommitCallbacks(execute=True):
            result = commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(result["recordings"], 3)
        self.assertEqual(result["image_attachments"], 0)

    def test_upload_rejects_invalid_batches_and_requires_csrf(self):
        draft = self.draft()
        self.stage(draft, image_upload(), SimpleUploadedFile("invalid.png", b"invalid"))
        self.assertEqual(draft.shared_images.count(), 0)
        self.assertFalse(list(self.root.glob("*.webp")))
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        self.assertEqual(protected.post(self.images_url(draft), {"image_action": "upload"}).status_code, 403)

    def test_removing_staged_image_invalidates_review_and_deletes_its_file(self):
        draft = self.draft()
        self.stage(draft)
        self.review(draft)
        image = draft.shared_images.select_related("storage_location").get()
        path = image_path(image)
        response = self.client.get(self.url(draft), {"step": "setup"})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.images_url(draft), {"image_action": "remove", "image_id": image.pk, "revision": response.context["revision"]})
        draft.refresh_from_db()
        self.assertNotIn("review_fingerprint", draft.state)
        self.assertFalse(path.exists())

    def test_storage_folder_change_does_not_move_existing_staged_images(self):
        draft = self.draft()
        self.stage(draft)
        first = draft.shared_images.select_related("storage_location").get()
        other = self.root / "new"
        other.mkdir()
        activate_directory(str(other))
        self.stage(draft, image_upload("second.png"))
        self.review(draft)
        commit_import(draft.pk, self.user, self.experiment, [])
        locations = set(RecordingImage.objects.values_list("storage_location__directory", flat=True))
        self.assertEqual(locations, {str(self.root), str(other)})
        self.assertTrue(image_path(first).exists())

    def test_ajax_upload_returns_preview_and_fresh_revision_without_overwriting_settings(self):
        draft = self.draft()
        page = self.client.get(self.url(draft), {"step": "setup"})
        configuration = draft.configuration.copy()
        response = self.client.post(self.images_url(draft), {"revision": page.context["revision"],
            "image_action": "upload", "images": image_upload()}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotEqual(payload["revision"], page.context["revision"])
        self.assertEqual(payload["count"], 1)
        self.assertIn('data-media-remove', payload["html"])
        draft.refresh_from_db()
        self.assertEqual(draft.configuration, configuration)
        result = self.client.post(self.url(draft), {"step": "setup", "revision": payload["revision"],
            "next_step": "mapping", "date_order": "DMY", "grouping": "date"})
        self.assertEqual(result.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.configuration["grouping"], "date")
        self.assertEqual(draft.shared_images.count(), 1)
