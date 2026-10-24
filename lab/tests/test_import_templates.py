from copy import deepcopy

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from ..models import Experiment, Project, RecordingField, RecordingType, SpreadsheetImportDraft, SpreadsheetImportTemplate
from ..spreadsheet_import.engine import digest, draft_table
from ..spreadsheet_import.forms import MappingForm
from ..spreadsheet_import.mapping import new_definition
from ..spreadsheet_import.source import resolve_binding, table_from_sheet
from ..spreadsheet_import.templates import capture_mapping, match_mapping
from ..test_navigation import Markup


@override_settings(ALLOWED_HOSTS=["testserver"])
class ImportTemplateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="template-author")
        cls.other = get_user_model().objects.create_user(username="template-user")
        cls.kind = RecordingType.objects.create(name="Template recording", slug="template-recording")
        cls.other_kind = RecordingType.objects.create(name="Another type", slug="template-other")
        cls.field = RecordingField.objects.create(recording_type=cls.kind, key="frames", label="Frames", data_type="integer")
        cls.project = Project.objects.create(name="Template project")
        cls.experiment = Experiment.objects.create(project=cls.project, recording_type=cls.kind, name="Template experiment")

    def setUp(self):
        self.client.force_login(self.user)

    def table(self, headers, merges=None):
        return table_from_sheet({"rows": [headers, ["value"] * len(headers)], "merges": merges or []}, 1)

    def template(self, mapping=None, **kwargs):
        return SpreadsheetImportTemplate.objects.create(
            recording_type=kwargs.pop("recording_type", self.kind),
            name=kwargs.pop("name", "Standard sheet"), created_by=self.user,
            mapping=mapping or {"version": 1, "bindings": {"recording_date": {"heading": "Date", "occurrence": 1, "count": 1}}},
            **kwargs,
        )

    def draft(self):
        source = [{"name": "CSV", "rows": [
            ["Date", "Mouse ID", "Frames", "Folder", "When", "New animal"],
            ["2026-10-24", "M1", "12", "/data", "2026-10-23", "M2"],
        ], "merges": []}]
        table = table_from_sheet(source[0], 1)
        return SpreadsheetImportDraft.objects.create(
            owner=self.user, experiment=self.experiment, filename="template.csv", source=source,
            configuration=new_definition(self.experiment, table["columns"]),
            state={"sheet": "CSV", "header_row": 1, "selected_rows": [2], "animals": {"old": "choice"}, "review_fingerprint": "old"},
        )

    def url(self, draft):
        return reverse("lab:import_recordings_draft", args=[self.project.pk, self.experiment.pk, draft.pk])

    def post_data(self, draft, **overrides):
        form = MappingForm(draft, draft_table(draft))
        data = {name: form[name].value() if form[name].value() is not None else "" for name in form.fields}
        data.update(step="mapping", revision=digest([draft.configuration, draft.state]))
        data.update(overrides)
        return data

    def test_capture_only_current_associations_and_animal_fields(self):
        columns = self.table(["Date", "Pedigree", "", "Frames"], ["B1:C1"])["columns"]
        payload = capture_mapping(self.experiment, columns, {
            "column_recording_date": "0", "column_animal:identifier": "2", "column_field:frames": "3",
            "mode_field:frames": "shared", "default_field:frames": "42", "protocol_id": "12",
        })
        self.assertEqual(payload, {"version": 1, "bindings": {
            "recording_date": {"heading": "Date", "occurrence": 1, "count": 1},
            "animal:identifier": {"heading": "Pedigree", "occurrence": 2, "count": 2},
        }})
        for data in ({}, {"column_recording_date": "999"}, {"mode_recording_date": "invalid"}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                capture_mapping(self.experiment, columns, data)

    def test_named_and_merged_columns_can_move(self):
        columns = self.table([" Date ", "Pedigree", ""], ["B1:C1"])["columns"]
        template = self.template(capture_mapping(self.experiment, columns, {
            "column_recording_date": "0", "column_animal:identifier": "2",
        }))
        moved = self.table(["Extra", "Pedigree", "", "Date"], ["B1:C1"])["columns"]
        bindings, warnings = match_mapping(template, self.experiment, moved)
        self.assertEqual(warnings, [])
        self.assertEqual(resolve_binding(moved, bindings["recording_date"]), 3)
        self.assertEqual(resolve_binding(moved, bindings["animal:identifier"]), 2)

    def test_changed_duplicate_counts_and_missing_headers_require_remapping(self):
        columns = self.table(["Pedigree", "Pedigree"])["columns"]
        template = self.template(capture_mapping(self.experiment, columns, {"column_animal:identifier": "1"}))
        for headers in (["Pedigree"], ["Pedigree"] * 3, ["pedigree"] * 2, ["Other"]):
            with self.subTest(headers=headers):
                resolved, warnings = match_mapping(template, self.experiment, self.table(headers)["columns"])
                self.assertEqual(resolved, {})
                self.assertEqual(len(warnings), 1)

    def test_unnamed_columns_require_same_position_and_count(self):
        columns = self.table(["Date", "", "Other", ""])["columns"]
        template = self.template(capture_mapping(self.experiment, columns, {"column_notes": "1"}))
        self.assertEqual(template.mapping["bindings"]["notes"]["index"], 1)
        resolved, warnings = match_mapping(template, self.experiment, columns)
        self.assertEqual(resolve_binding(columns, resolved["notes"]), 1)
        self.assertEqual(warnings, [])
        for headers in (["", "Date", "Other", ""], ["Date", "", "Other"]):
            with self.subTest(headers=headers):
                resolved, warnings = match_mapping(template, self.experiment, self.table(headers)["columns"])
                self.assertEqual(resolved, {})
                self.assertTrue(warnings)

    def test_inactive_or_deleted_fields_are_skipped(self):
        columns = self.table(["Frames"])["columns"]
        template = self.template(capture_mapping(self.experiment, columns, {"column_field:frames": "0"}))
        self.field.active = False
        self.field.save(update_fields=["active"])
        for delete in (False, True):
            if delete:
                self.field.delete()
            resolved, warnings = match_mapping(template, self.experiment, columns)
            self.assertEqual(resolved, {})
            self.assertIn("no longer available", warnings[0])

    def test_wrong_type_and_unsupported_versions_are_rejected(self):
        template = self.template()
        template.recording_type = self.other_kind
        with self.assertRaises(ValidationError):
            match_mapping(template, self.experiment, [])
        template.recording_type = self.kind
        for mapping in ({"version": 100}, {"version": 1, "bindings": {"notes": None}}):
            template.mapping = mapping
            with self.assertRaises(ValidationError):
                match_mapping(template, self.experiment, [])

    def test_name_uniqueness_is_enforced_in_database_per_type(self):
        self.template(name=" Original ")
        with self.assertRaises(IntegrityError), transaction.atomic():
            SpreadsheetImportTemplate.objects.bulk_create([
                SpreadsheetImportTemplate(recording_type=self.kind, name=" ORIGINAL "),
            ])
        self.template(name="Original", recording_type=self.other_kind)
        self.assertEqual(SpreadsheetImportTemplate.objects.count(), 2)

    def test_save_uses_unsaved_selections_and_allows_partial_mapping(self):
        draft = self.draft()
        before = deepcopy(draft.configuration)
        data = self.post_data(draft, template_action="save", **{
            "template-name": " My sheet ", "column_recording_date": "4",
            "column_animal:identifier": "", "column_field:frames": "",
            "mode_notes": "shared", "default_notes": "Do not store this",
        })
        response = self.client.post(self.url(draft), data)
        self.assertEqual(response.status_code, 200)
        template = SpreadsheetImportTemplate.objects.get()
        self.assertEqual(template.name, "My sheet")
        self.assertEqual(template.created_by, self.user)
        self.assertEqual(template.mapping["bindings"]["recording_date"]["heading"], "When")
        self.assertNotIn("animal:identifier", template.mapping["bindings"])
        self.assertNotIn("defaults", template.mapping)
        self.assertNotContains(response, "Map an Animal ID or Local identifier")
        self.assertContains(response, "Do not store this")
        draft.refresh_from_db()
        self.assertEqual(draft.configuration, before)

    def test_template_controls_are_compact_and_name_is_only_in_separate_dialog(self):
        draft = self.draft()
        response = self.client.get(self.url(draft), {"step": "mapping"})
        markup = Markup(response.content.decode())
        name = markup.find("input", name="template-name")[0]
        self.assertTrue(name.within("dialog", id="import-template-dialog"))
        self.assertFalse(name.within("form", **{"data-import-form": None}))
        self.assertIn("required", name.attrs)
        self.assertNotContains(response, "Apply template</button>")
        self.assertEqual(markup.find("button", **{"data-import-template-open": None})[0].attrs["type"], "button")
        response = self.client.post(self.url(draft), self.post_data(draft, next_step="animals"))
        self.assertRedirects(response, self.url(draft) + "?step=animals", fetch_redirect_response=False)

    def test_save_rejects_empty_duplicate_and_blank_name_without_changing_draft(self):
        draft = self.draft()
        template = self.template()
        for name in ("", " STANDARD SHEET "):
            response = self.client.post(self.url(draft), self.post_data(draft, template_action="save", **{"template-name": name}))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["template_name_form"].errors)
            self.assertTrue(response.context["template_dialog_open"])
        empty = self.post_data(draft, template_action="save", **{"template-name": "Empty"})
        for key in empty:
            if key.startswith("column_"):
                empty[key] = ""
        response = self.client.post(self.url(draft), empty)
        self.assertContains(response, "Select at least one source column")
        self.assertEqual(list(SpreadsheetImportTemplate.objects.all()), [template])

    def test_apply_replaces_automatic_matches_and_preserves_other_settings(self):
        draft = self.draft()
        draft.configuration["defaults"].update(notes="Keep notes", recording_date="2026-01-01")
        draft.configuration.update(date_order="YMD", grouping="date", procedure_drafts=[{"kept": True}])
        draft.save()
        template = self.template(capture_mapping(self.experiment, draft_table(draft)["columns"], {
            "column_recording_date": "4", "column_animal:identifier": "5",
        }))
        response = self.client.post(self.url(draft), self.post_data(draft, template_action="apply", import_template=template.pk))
        self.assertRedirects(response, self.url(draft) + "?step=mapping", fetch_redirect_response=False)
        draft.refresh_from_db()
        self.assertEqual(draft.configuration["bindings"], {
            "recording_date": {"heading": "When", "occurrence": 1},
            "animal:identifier": {"heading": "New animal", "occurrence": 1},
        })
        self.assertEqual(draft.configuration["defaults"]["recording_date"], "")
        self.assertEqual(draft.configuration["defaults"]["notes"], "Keep notes")
        self.assertEqual(draft.configuration["date_order"], "YMD")
        self.assertEqual(draft.configuration["grouping"], "date")
        self.assertEqual(draft.configuration["procedure_drafts"], [{"kept": True}])
        self.assertNotIn("review_fingerprint", draft.state)
        self.assertNotIn("animals", draft.state)
        template.delete()
        draft.refresh_from_db()
        self.assertEqual(draft.configuration["bindings"]["recording_date"]["heading"], "When")

    def test_apply_keeps_animal_resolutions_when_identity_does_not_change(self):
        draft = self.draft()
        template = self.template(capture_mapping(self.experiment, draft_table(draft)["columns"], {"column_animal:identifier": "1"}))
        response = self.client.post(self.url(draft), self.post_data(draft, template_action="apply", import_template=template.pk))
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.state["animals"], {"old": "choice"})
        self.assertNotIn("review_fingerprint", draft.state)

    def test_mapping_order_on_load_and_after_applying_template(self):
        self.field.required = True
        self.field.save(update_fields=["required"])
        RecordingField.objects.create(recording_type=self.kind, key="missing", label="Missing result", data_type="text", required=True)
        RecordingField.objects.create(recording_type=self.kind, key="zero", label="Zero value", data_type="integer", required=True, default_value=0)
        RecordingField.objects.create(recording_type=self.kind, key="flag", label="Flag", data_type="boolean", default_value=False)
        draft = self.draft()
        response = self.client.get(self.url(draft), {"step": "mapping"})
        rows = response.context["form"].mapping_rows
        self.assertEqual(rows[0]["column"].name, "column_field:missing")
        self.assertEqual([2 if row["associated"] else 0 if row["required"] else 1 for row in rows],
                         sorted(2 if row["associated"] else 0 if row["required"] else 1 for row in rows))
        for key in ("zero", "flag"):
            self.assertTrue(next(row for row in rows if row["column"].name == f"column_field:{key}")["associated"])
        template = self.template(capture_mapping(self.experiment, draft_table(draft)["columns"], {"column_recording_date": "4"}))
        response = self.client.post(self.url(draft), self.post_data(draft, template_action="apply", import_template=template.pk), follow=True)
        rows = response.context["form"].mapping_rows
        self.assertEqual([row["column"].name for row in rows[:2]], ["column_field:frames", "column_field:missing"])
        self.assertEqual([2 if row["associated"] else 0 if row["required"] else 1 for row in rows],
                         sorted(2 if row["associated"] else 0 if row["required"] else 1 for row in rows))
        self.assertTrue(next(row for row in rows if row["column"].name == "column_recording_date")["associated"])

    def test_bound_mapping_order_uses_unsaved_changes(self):
        draft = self.draft()
        data = self.post_data(draft, **{
            "column_recording_date": "", "mode_notes": "shared", "default_notes": "New notes",
        })
        form = MappingForm(draft, draft_table(draft), data=data)
        self.assertEqual(form.mapping_rows[0]["column"].name, "column_recording_date")
        self.assertFalse(form.mapping_rows[0]["associated"])
        self.assertTrue(next(row for row in form.mapping_rows if row["column"].name == "column_notes")["associated"])

    def test_apply_missing_columns_warns_and_does_not_restore_automatic_match(self):
        draft = self.draft()
        template = self.template({"version": 1, "bindings": {"recording_date": {"heading": "Missing", "occurrence": 1, "count": 1}}})
        response = self.client.post(self.url(draft), self.post_data(draft, template_action="apply", import_template=template.pk), follow=True)
        self.assertContains(response, "is missing. Choose a source column.")
        draft.refresh_from_db()
        self.assertNotIn("recording_date", draft.configuration["bindings"])

    def test_only_relevant_templates_are_offered_and_other_types_cannot_be_applied(self):
        draft = self.draft()
        own = self.template(name="Relevant template")
        foreign = self.template(recording_type=self.other_kind, name="Irrelevant template")
        response = self.client.get(self.url(draft), {"step": "mapping"})
        self.assertContains(response, own.name)
        self.assertNotContains(response, foreign.name)
        before = deepcopy(draft.configuration)
        for value in (foreign.pk, "invalid", "", 999999):
            response = self.client.post(self.url(draft), self.post_data(draft, template_action="apply", import_template=value))
            self.assertContains(response, "Choose an available template")
        draft.refresh_from_db()
        self.assertEqual(draft.configuration, before)

    def test_stale_revision_rejects_both_template_actions(self):
        draft = self.draft()
        template = self.template()
        for action in ("save", "apply"):
            response = self.client.post(self.url(draft), self.post_data(draft,
                template_action=action, revision="stale", import_template=template.pk, **{"template-name": "New template"}))
            self.assertContains(response, "changed in another tab")
        self.assertEqual(SpreadsheetImportTemplate.objects.count(), 1)
        draft.refresh_from_db()
        self.assertEqual(draft.state["review_fingerprint"], "old")

    def test_shared_settings_allow_rename_and_confirmed_delete(self):
        template = self.template()
        self.client.force_login(self.other)
        for route in ("home", "settings_page", "import_templates"):
            response = self.client.get(reverse(f"lab:{route}"))
            self.assertEqual(response.status_code, 200)
        self.assertContains(self.client.get(reverse("lab:home")), reverse("lab:settings_page"))
        self.assertContains(response, template.name)
        rename = reverse("lab:rename_import_template", args=[template.pk])
        self.assertEqual(self.client.get(rename).status_code, 200)
        template.refresh_from_db()
        self.assertEqual(template.name, "Standard sheet")
        response = self.client.post(rename, {"name": "Renamed"})
        self.assertRedirects(response, reverse("lab:import_templates"))
        template.refresh_from_db()
        self.assertEqual(template.name, "Renamed")
        self.assertEqual(template.created_by, self.user)
        delete = reverse("lab:delete_import_template", args=[template.pk])
        self.assertContains(self.client.get(delete), "Mappings already applied to imports will be kept.")
        self.assertTrue(SpreadsheetImportTemplate.objects.filter(pk=template.pk).exists())
        self.assertRedirects(self.client.post(delete), reverse("lab:import_templates"))
        self.assertFalse(SpreadsheetImportTemplate.objects.exists())

    def test_rename_rejects_duplicate_name(self):
        first = self.template()
        second = self.template(name="Second")
        response = self.client.post(reverse("lab:rename_import_template", args=[second.pk]), {"name": f" {first.name.upper()} "})
        self.assertContains(response, "already exists for this experiment type")
        second.refresh_from_db()
        self.assertEqual(second.name, "Second")

    def test_settings_routes_require_login_and_post_actions_require_csrf(self):
        template = self.template()
        routes = [("settings_page", []), ("import_templates", []), ("rename_import_template", [template.pk]), ("delete_import_template", [template.pk])]
        anonymous = Client()
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        for route, args in routes:
            url = reverse(f"lab:{route}", args=args)
            self.assertEqual(anonymous.get(url).status_code, 302)
            self.assertEqual(protected.post(url, {"name": "Changed"}).status_code, 403)
        draft = self.draft()
        self.assertEqual(protected.post(self.url(draft), self.post_data(draft, template_action="save")).status_code, 403)
        template.refresh_from_db()
        self.assertEqual(template.name, "Standard sheet")
