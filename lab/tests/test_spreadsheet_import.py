import io
import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ..models import (
    Crossing, CrossingLine, Experiment, GenotypeCategory, Mouse, MouseGenotype, MouseLine, Project, Protocol,
    Procedure, ProcedureType, Recording, RecordingField, RecordingType, Species, SpreadsheetImportDraft, resolve_crossing,
)
from ..test_navigation import Markup
from ..spreadsheet_import.engine import animal_groups, build_review, commit_import, draft_table, initial_resolution
from ..spreadsheet_import.forms import AnimalsForm, GeneticsForm, MappingForm
from ..spreadsheet_import.mapping import crossing_candidates, crossing_suggestion, new_definition, parse_date, validate_definition
from ..spreadsheet_import.source import binding, read_upload, table_from_sheet


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class SpreadsheetImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="importer")
        cls.other = get_user_model().objects.create_user(username="another-importer")
        cls.protocol = Protocol.objects.create(protocol_number=990, licence_reference="IMPORT", name="Import protocol")
        cls.species = Species.objects.get(name="Mouse")
        cls.line = MouseLine.objects.create(species=cls.species, name="Import line", aliases="IL, Legacy line")
        cls.crossing = cls.line.crossings.get()
        cls.mouse = Mouse.objects.create(mouse_id="OFFICIAL-1", tattoo="LE", date_of_birth=date(2026, 1, 1), protocol=cls.protocol, crossing_definition=cls.crossing)
        MouseGenotype.objects.create(mouse=cls.mouse, mouse_line=cls.line, zygosity="unknown")
        cls.recording_type = RecordingType.objects.create(name="Import test", slug="import-test")
        cls.field = RecordingField.objects.create(recording_type=cls.recording_type, key="frames", label="Frames", data_type="integer")
        cls.project = Project.objects.create(name="Import project")
        cls.experiment = Experiment.objects.create(project=cls.project, name="Import experiment", recording_type=cls.recording_type)

    def setUp(self):
        self.client.force_login(self.user)

    def draft(self, text="Date,Mouse ID,Folder,Frames\n28.04.26,OFFICIAL-1,/a,10\n28.04.26,OFFICIAL-1,/b,10\n"):
        source = read_upload(SimpleUploadedFile("data.csv", text.encode()))
        table = table_from_sheet(source[0], 1)
        draft = SpreadsheetImportDraft.objects.create(
            owner=self.user, experiment=self.experiment, filename="data.csv", source=source,
            configuration=new_definition(self.experiment, table["columns"]),
            state={"sheet": "CSV", "header_row": 1, "selected_rows": [r["number"] for r in table["rows"]]},
        )
        draft.state["animals"] = {g["key"]: initial_resolution(g, draft.configuration) for g in animal_groups(draft)}
        draft.save()
        return draft

    def reviewed(self, draft):
        preview = build_review(draft)
        self.assertEqual(preview["errors"], [])
        draft.state["review_fingerprint"] = preview["fingerprint"]
        draft.save()
        return preview

    def url(self, draft=None):
        return reverse("lab:import_recordings_draft" if draft else "lab:import_recordings", args=[self.project.pk, self.experiment.pk, *([draft.pk] if draft else [])])

    def test_supplied_genotyping_workbook(self):
        path = Path(__file__).resolve().parents[2] / "docs" / "EAR CLIP SHEET Gria2 x GCaMP6f x NeuroD1-cre 16.10.26.xlsx"
        with path.open("rb") as file:
            source = read_upload(file)
        table = table_from_sheet(source[0], 1)
        self.assertEqual(len(table["rows"]), 9)
        self.assertEqual(table["rows"][0]["flag"], "Possible metadata row")
        self.assertEqual(table["columns"][1]["heading"], "Pedigree #")
        self.assertEqual(table["columns"][9]["occurrence"], 1)
        self.assertEqual(table["columns"][11]["occurrence"], 3)
        self.assertEqual(table["rows"][1]["values"][4], "2026-10-02")
        self.assertEqual(sum(not r["flag"] for r in table["rows"]), 8)

    def test_supplied_imaging_workbook(self):
        path = Path(__file__).resolve().parents[2] / "docs" / "Alpha syn Myo15_iAchSnFr.xlsx"
        with path.open("rb") as file:
            source = read_upload(file)
        table = table_from_sheet(source[0], 1)
        self.assertEqual(len(table["rows"]), 50)
        self.assertEqual(len({r["values"][1] for r in table["rows"]}), 12)
        self.assertEqual(len({r["values"][4] for r in table["rows"]}), 50)
        self.assertEqual(len({parse_date(r["values"][0]) for r in table["rows"]}), 5)

    def test_csv_duplicate_columns_empty_metadata_and_utf8(self):
        source = read_upload(SimpleUploadedFile("data.csv", b"\xef\xbb\xbfMouse ID;Genotype;Genotype\nnote;;\nOFFICIAL-1;Het;Hom\n;;\n"))
        table = table_from_sheet(source[0], 1)
        self.assertEqual([c["occurrence"] for c in table["columns"]], [1, 1, 2])
        self.assertEqual([r["flag"] for r in table["rows"]], ["Possible metadata row", "", "Empty row"])

    def test_invalid_and_oversized_uploads(self):
        for upload in (SimpleUploadedFile("bad.xlsx", b"bad"), SimpleUploadedFile("old.xls", b"bad"), SimpleUploadedFile("huge.csv", b"a" * (10 * 1024 * 1024 + 1))):
            with self.assertRaises(ValidationError):
                read_upload(upload)

    def test_text_dates_and_explicit_date_order(self):
        self.assertEqual(parse_date("7.5.26"), date(2026, 5, 7))
        self.assertEqual(parse_date("10/02/2026", "MDY"), date(2026, 10, 2))
        self.assertEqual(parse_date("2026/10/02", "YMD"), date(2026, 10, 2))
        with self.assertRaises(ValidationError):
            parse_date("31/02/26")

    def test_portable_mapping_reorders_columns_without_positions(self):
        draft = self.draft()
        definition = json.loads(json.dumps(draft.configuration))
        self.assertNotIn("index", definition["bindings"]["recording_date"])
        new_source = read_upload(SimpleUploadedFile("other.csv", b"Frames,Folder,Mouse ID,Date\n20,/new,OFFICIAL-1,30.04.26\n"))
        draft.source = new_source
        draft.state["selected_rows"] = [2]
        draft.configuration = definition
        preview = self.reviewed(draft)
        self.assertEqual(preview["records"][0]["values"], {"frames": 20})
        self.assertEqual(preview["records"][0]["scalars"]["recording_date"], "2026-04-30")

    def test_missing_column_and_changed_type_require_remapping(self):
        draft = self.draft()
        columns = draft_table(draft)["columns"]
        with self.assertRaises(ValidationError):
            validate_definition(draft.configuration, self.experiment, columns[1:])
        draft.configuration["recording_type"] = "other"
        with self.assertRaises(ValidationError):
            validate_definition(draft.configuration, self.experiment, columns)

    def test_unavailable_references_are_revalidated(self):
        for key, value in (("protocol_id", 999999), ("crossing_id", 999999), ("species_id", 999999)):
            draft = self.draft()
            draft.configuration["defaults"][key] = value
            with self.assertRaises(ValidationError):
                build_review(draft)
        draft = self.draft()
        draft.configuration["genotype_maps"] = {str(self.line.pk): {"Hett": "not-a-category"}}
        with self.assertRaises(ValidationError):
            build_review(draft)

    def test_preview_does_not_write_and_repeated_identifier_resolves_once(self):
        draft = self.draft()
        before = (Mouse.objects.count(), Recording.objects.count(), MouseGenotype.objects.count())
        preview = build_review(draft)
        self.assertEqual(preview["errors"], [])
        self.assertEqual(len(preview["animals"]), 1)
        self.assertEqual(len(preview["records"]), 2)
        self.assertEqual(before, (Mouse.objects.count(), Recording.objects.count(), MouseGenotype.objects.count()))

    def test_separate_rows_save_typed_fields_paths_and_owner(self):
        draft = self.draft()
        self.reviewed(draft)
        result = commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(result["recordings"], 2)
        self.assertEqual(result["created_animals"], 0)
        records = list(self.experiment.recordings.order_by("pk"))
        self.assertEqual(records[0].values, {"frames": 10})
        self.assertEqual(records[0].data_paths, ["/a"])
        self.assertEqual(records[0].owner, self.user)
        self.assertEqual(list(records[0].mice.all()), [self.mouse])

    def test_combine_by_date_and_union_paths(self):
        draft = self.draft("Date,Mouse ID,Folder,Frames\n28.04.26,OFFICIAL-1,/a,10\n28.04.26,OFFICIAL-1,/b,\n30.04.26,OFFICIAL-1,/a,20\n")
        draft.configuration["grouping"] = "date"
        preview = self.reviewed(draft)
        self.assertEqual(len(preview["records"]), 2)
        self.assertEqual(preview["records"][0]["paths"], ["/a", "/b"])
        result = commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(result["recordings"], 2)

    def test_combined_conflict_identifies_rows_and_field(self):
        draft = self.draft("Date,Mouse ID,Frames\n28.04.26,OFFICIAL-1,10\n28.04.26,OFFICIAL-1,20\n")
        draft.configuration["grouping"] = "date"
        preview = build_review(draft)
        self.assertTrue(any("frames" in e.casefold() and "2" in e and "3" in e for e in preview["errors"]))

    def test_invalid_required_and_boolean_fields(self):
        self.field.required = True
        self.field.save()
        draft = self.draft("Date,Mouse ID,Frames\n28.04.26,OFFICIAL-1,\n")
        self.assertTrue(build_review(draft)["errors"])
        self.field.data_type = "boolean"
        self.field.required = False
        self.field.save()
        draft = self.draft("Date,Mouse ID,Frames\n28.04.26,OFFICIAL-1,perhaps\n")
        self.assertTrue(any("Yes/No" in e for e in build_review(draft)["errors"]))

    def test_create_official_animal_with_protocol_and_crossing(self):
        draft = self.draft("Date,Mouse ID\n28.04.26,NEW-ID\n")
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk)
        self.reviewed(draft)
        result = commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(result["created_animals"], 1)
        mouse = Mouse.objects.get(pk="NEW-ID")
        self.assertEqual(mouse.added_by, self.user)
        self.assertEqual(mouse.protocol, self.protocol)
        self.assertEqual(mouse.genotype_calls.get().zygosity, "unknown")

    def test_missing_creation_details_are_errors(self):
        draft = self.draft("Date,Mouse ID\n28.04.26,NEW-ID\n")
        self.assertTrue(build_review(draft)["errors"])
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk)
        draft = self.draft("Date,Local identifier\n28.04.26,LE\n")
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk)
        self.assertTrue(any("date_of_birth" in e for e in build_review(draft)["errors"]))

    def test_local_identifier_needs_confirmation_and_can_generate_id(self):
        draft = self.draft("Date,Local identifier,Birth Date,Breeding pair\n28.04.26,LOCAL,01.04.26,PAIR\n")
        group = animal_groups(draft)[0]
        self.assertEqual(initial_resolution(group, draft.configuration)["mode"], "create")
        draft.state["animals"] = {group["key"]: initial_resolution(group, draft.configuration)}
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk)
        preview = self.reviewed(draft)
        self.assertIn("20260401", preview["animals"][0]["mouse_id"])
        commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(Mouse.objects.get(tattoo="LOCAL").id_status, Mouse.ID_TEMPORARY)
        self.assertEqual(Mouse.objects.get(tattoo="LOCAL").marking_type, "other")

    def test_additional_identity_columns_split_reused_local_identifiers(self):
        draft = self.draft("Date,Local identifier\n28.04.26,LE\n30.04.26,LE\n")
        self.assertEqual(len(animal_groups(draft)), 1)
        draft.configuration["identity_columns"] = [binding(draft_table(draft)["columns"][0])]
        self.assertEqual(len(animal_groups(draft)), 2)

    def form_data(self, form):
        return {name: form[name].value() if form[name].value() is not None else "" for name in form.fields}

    def test_identifier_mappings_have_fixed_labels_without_an_interpretation_selector(self):
        draft = self.draft()
        response = self.client.get(self.url(draft), {"step": "setup"})
        self.assertNotContains(response, "Source animal identifier means")
        self.assertNotIn("identifier_kind", response.context["form"].fields)
        self.assertNotContains(response, "Local identifier type (tattoo, ear clip)")
        response = self.client.get(self.url(draft), {"step": "mapping"})
        identities = [row for row in response.context["form"].mapping_rows if row["identity"]]
        self.assertCountEqual([row["label"] for row in identities], ["Animal ID (unique)", "Local identifier (tattoo, ear clip)"])
        self.assertFalse(any(row["required"] for row in identities))
        self.assertEqual(draft.configuration["version"], 2)
        self.assertNotIn("identifier_kind", draft.configuration)

    def test_mixed_rows_use_supplied_animal_ids_and_generate_only_when_missing(self):
        self.mouse.marking_type = "tattoo"
        self.mouse.save(update_fields=["marking_type"])
        draft = self.draft("Date,Animal ID,Local identifier,Birth Date,Breeding pair\n28.04.26,OFFICIAL-1,NEW-MARK,1.4.26,PAIR\n28.04.26,SUPPLIED-ID,LOCAL-X,1.4.26,PAIR\n28.04.26,,LOCAL-Y,1.4.26,PAIR\n")
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk, marking_type="ear_clip")
        groups = animal_groups(draft)
        resolutions = [draft.state["animals"][group["key"]] for group in groups]
        self.assertEqual([(row["mode"], row.get("generate_id", False)) for row in resolutions], [("link", False), ("create", False), ("create", True)])
        preview = self.reviewed(draft)
        self.assertEqual([animal["mouse_id"] for animal in preview["animals"][:2]], ["OFFICIAL-1", "SUPPLIED-ID"])
        generated = preview["animals"][2]["mouse_id"]
        self.assertIn("20260401", generated)
        self.assertTrue(preview["animals"][2]["generate_id"])
        result = commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(result["created_animals"], 2)
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.marking_type, "tattoo")
        self.assertEqual(Mouse.objects.get(pk="SUPPLIED-ID").marking_type, "other")
        self.assertEqual(self.mouse.tattoo, "LE")  # Proposed marking changes still need approval.
        self.assertEqual(Mouse.objects.get(pk="SUPPLIED-ID").tattoo, "LOCAL-X")
        self.assertEqual(Mouse.objects.get(pk=generated).tattoo, "LOCAL-Y")

    def test_local_id_candidates_require_an_explicit_link_and_never_match_as_animal_ids(self):
        # The marking LE differs from the Animal ID, and another animal even
        # has that marking as its unique ID. Neither is automatically linked.
        Mouse.objects.create(mouse_id="LE", protocol=self.protocol, crossing_definition=self.crossing)
        draft = self.draft("Date,Local identifier\n28.04.26,LE\n")
        form = AnimalsForm(draft, draft_table(draft))
        group = form.groups[0]
        self.assertEqual(group["mode_field"].value(), "generate")
        self.assertEqual([mouse.pk for mouse in group["candidates"]], [self.mouse.pk])
        data = self.form_data(form)
        data.update({f"{group['key']}_mode": "link", f"{group['key']}_mouse_id": self.mouse.pk})
        selected = AnimalsForm(draft, draft_table(draft), data=data)
        self.assertTrue(selected.is_valid(), selected.errors)
        selected.apply(draft)
        preview = self.reviewed(draft)
        self.assertEqual(preview["animals"][0]["mouse_id"], self.mouse.pk)
        self.assertFalse(preview["animals"][0]["generate_id"])
        self.assertEqual(commit_import(draft.pk, self.user, self.experiment, [])["created_animals"], 0)

    def test_generated_id_preview_matches_import_without_creating_anything_early(self):
        draft = self.draft("Date,Local identifier,Birth Date,Breeding pair\n28.04.26,NEW-MARK,1.4.26,PAIR\n")
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk)
        draft.save()
        before = json.loads(json.dumps(draft.state))
        response = self.client.get(self.url(draft), {"step": "animals"})
        group = response.context["form"].groups[0]
        self.assertEqual(group["mode_field"].value(), "generate")
        self.assertIn("20260401", group["proposed_id"])
        self.assertContains(response, group["proposed_id"])
        self.assertNotContains(response, "Generate ID</span>")
        self.assertEqual(Mouse.objects.count(), 1)
        self.assertFalse(Recording.objects.exists())
        draft.refresh_from_db()
        self.assertEqual(draft.state, before)
        self.reviewed(draft)
        result = commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(result["created_animals"], 1)
        self.assertTrue(Mouse.objects.filter(pk=group["proposed_id"], tattoo="NEW-MARK").exists())

    def test_official_ids_ignore_distinguishing_columns_and_have_separate_local_groups(self):
        draft = self.draft("Date,Animal ID,Local identifier\n28.04.26,LE,LE\n28.04.26,,LE\n30.04.26,,LE\n30.04.26,le,LE\n")
        draft.configuration["identity_columns"] = [binding(draft_table(draft)["columns"][0])]
        groups = animal_groups(draft)
        self.assertEqual(len(groups), 3)
        self.assertEqual([row["number"] for row in groups[0]["rows"]], [2, 5])
        self.assertEqual(groups[0]["extra"], [])
        self.assertTrue(all(group["identifier_kind"] == "local" for group in groups[1:]))
        self.assertEqual(len({group["key"] for group in groups}), 3)

    def test_mapping_requires_either_identifier_and_blank_rows_do_not_merge(self):
        draft = self.draft("Date,Frames\n28.04.26,10\n")
        form = MappingForm(draft, draft_table(draft))
        bound = MappingForm(draft, draft_table(draft), data=self.form_data(form))
        self.assertFalse(bound.is_valid())
        self.assertIn("column_animal:identifier", bound.errors)
        draft = self.draft("Date,Animal ID,Local identifier\n28.04.26,,\n28.04.26,,\n")
        self.assertEqual(len(animal_groups(draft)), 2)
        errors = build_review(draft)["errors"]
        self.assertTrue(any("Rows 2:" in error and "Animal ID or Local identifier" in error for error in errors))
        self.assertTrue(any("Rows 3:" in error and "Animal ID or Local identifier" in error for error in errors))

    def test_supplied_animal_ids_cannot_be_silently_replaced_by_generation(self):
        draft = self.draft("Date,Animal ID,Local identifier,Birth Date,Breeding pair\n28.04.26,SUPPLIED-ID,LE,1.4.26,PAIR\n")
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk)
        group = animal_groups(draft)[0]
        form = AnimalsForm(draft, draft_table(draft))
        self.assertNotIn("generate", dict(form.fields[f"{group['key']}_mode"].choices))
        data = self.form_data(form)
        data[f"{group['key']}_mode"] = "generate"
        bound = AnimalsForm(draft, draft_table(draft), data=data)
        self.assertFalse(bound.is_valid())
        draft.state["animals"][group["key"]]["generate_id"] = True
        self.assertTrue(any("An Animal ID was supplied" in error for error in build_review(draft)["errors"]))
        self.assertFalse(Recording.objects.exists())

    def test_generation_action_ignores_leftover_manual_id_and_clears_migration_notice(self):
        draft = self.draft("Date,Local identifier\n28.04.26,LE\n")
        draft.state["identifier_mapping_updated"] = True
        form = AnimalsForm(draft, draft_table(draft))
        group = form.groups[0]
        data = self.form_data(form)
        data[f"{group['key']}_mouse_id"] = self.mouse.pk
        bound = AnimalsForm(draft, draft_table(draft), data=data)
        self.assertTrue(bound.is_valid(), bound.errors)
        bound.apply(draft)
        self.assertEqual(draft.state["animals"][group["key"]]["mouse_id"], "")
        self.assertTrue(draft.state["animals"][group["key"]]["generate_id"])
        self.assertNotIn("identifier_mapping_updated", draft.state)

    def test_identifier_configurations_are_reusable_after_column_reordering(self):
        first = self.draft("Date,Animal ID,Local identifier\n28.04.26,OFFICIAL-1,LE\n")
        second = self.draft("Local identifier,Animal ID,Date\nLE,OFFICIAL-1,29.04.26\n")
        second.configuration = json.loads(json.dumps(first.configuration))
        second.state["animals"] = {group["key"]: initial_resolution(group, second.configuration) for group in animal_groups(second)}
        preview = build_review(second)
        self.assertEqual(preview["errors"], [])
        self.assertEqual(preview["animals"][0]["mouse_id"], self.mouse.pk)
        self.assertNotIn("animals", second.configuration)

    def test_reviewed_changes_only_and_blank_preserves_value(self):
        draft = self.draft("Date,Mouse ID,Sex,Birth Date\n28.04.26,OFFICIAL-1,Female,\n")
        preview = self.reviewed(draft)
        self.assertEqual([c["field"] for c in preview["changes"]], ["sex"])
        commit_import(draft.pk, self.user, self.experiment, [])
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.sex, "")
        self.assertEqual(self.mouse.date_of_birth, date(2026, 1, 1))
        draft = self.draft("Date,Mouse ID,Sex\n28.04.26,OFFICIAL-1,Female\n")
        preview = self.reviewed(draft)
        commit_import(draft.pk, self.user, self.experiment, [preview["changes"][0]["token"]])
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.sex, "F")

    def test_alias_matching_and_ambiguous_aliases(self):
        self.assertEqual([c.pk for c in crossing_candidates("legacy LINE")], [self.crossing.pk])
        another = MouseLine.objects.create(species=self.species, name="Another", aliases="Legacy line")
        self.assertEqual(len(crossing_candidates("Legacy line")), 2)

    def test_crossing_suggestions_match_names_aliases_punctuation_and_clear_typos(self):
        for value in ("IMPORT LINE", "legacy LINE", "Import-line", "Import lnie", "Legacy lnie"):
            with self.subTest(value=value):
                self.assertEqual(crossing_suggestion(value, self.species.pk), self.crossing)
        # Approximate matches belong to the editable form, not import resolution.
        self.assertEqual(crossing_candidates("Import lnie"), [])

    def test_crossing_suggestions_leave_ambiguous_weak_and_inactive_matches_blank(self):
        other = MouseLine.objects.create(species=self.species, name="Import lime", aliases="Legacy line")
        for value in ("Legacy line", "Import lite", "unrelated strain", "ILx", ""):
            with self.subTest(value=value):
                self.assertIsNone(crossing_suggestion(value))
        self.assertIsNone(crossing_suggestion("Import line", -1))
        Crossing.objects.filter(pk=self.crossing.pk).update(active=False)
        Crossing.objects.filter(pk=other.crossings.get().pk).update(active=False)
        self.assertIsNone(crossing_suggestion("Import line"))

    def test_crossing_lookup_searches_aliases_and_returns_each_active_crossing_once(self):
        other = MouseLine.objects.create(species=self.species, name="Other lookup line", aliases="Legacy line")
        inactive = other.crossings.get()
        combined, _ = resolve_crossing(self.species, [self.line, other])
        Crossing.objects.filter(pk=inactive.pk).update(active=False)
        response = self.client.get(reverse("lab:crossing_lookup"), {"q": "lEgAcY LiNe"})
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertCountEqual([row["id"] for row in results], [self.crossing.pk, combined.pk])

    def test_all_import_crossing_fields_use_the_crossing_lookup(self):
        draft = self.draft("Date,Mouse ID,Strain\n28.04.26,OFFICIAL-1,Legacy line\n")
        genetics = GeneticsForm(draft, draft_table(draft))
        animals = AnimalsForm(draft, draft_table(draft))
        fields = [genetics.fields["crossing_id"], genetics.fields[genetics.crossing_rows[0]["name"]],
                  animals.fields[f"{animals.groups[0]['key']}_crossing_definition"]]
        for field in fields:
            self.assertEqual(str(field.widget.attrs["data-import-crossing-select"]), reverse("lab:crossing_lookup"))

    def test_prefilled_crossing_shows_genotype_columns_and_requires_saving(self):
        draft = self.draft("Date,Mouse ID,Strain,iMpOrT LiNe\n28.04.26,NEW-ID,Import lnie,Het\n")
        draft.configuration["defaults"]["protocol_id"] = self.protocol.pk
        draft.save()
        response = self.client.get(self.url(draft), {"step": "genetics"})
        form = response.context["form"]
        self.assertEqual(form[form.crossing_rows[0]["name"]].value(), self.crossing.pk)
        self.assertEqual([row["line"] for row in form.line_rows], [self.line])
        self.assertEqual(form[f"genotype_column_{self.line.pk}"].value(), "3")
        self.assertEqual(draft.configuration["crossing_map"], {})
        self.assertTrue(any("Map strain description" in error for error in build_review(draft)["errors"]))
        data = {name: form[name].value() if form[name].value() is not None else "" for name in form.fields}
        data.update(step="genetics", next_step="genetics", revision=response.context["revision"])
        response = self.client.post(self.url(draft), data, follow=True)
        self.assertEqual(response.status_code, 200)
        draft.refresh_from_db()
        self.assertEqual(draft.configuration["crossing_map"], {"Import lnie": self.crossing.pk})
        self.assertEqual(draft.configuration["bindings"][f"genotype:{self.line.pk}"], binding(draft_table(draft)["columns"][3]))
        self.assertEqual(build_review(draft)["errors"], [])
        self.assertFalse(Mouse.objects.filter(pk="NEW-ID").exists())

    def test_genotype_column_prefill_prefers_line_name_and_preserves_manual_choices(self):
        draft = self.draft("Date,Mouse ID,IL,IMPORT LINE,Import-line\n28.04.26,OFFICIAL-1,Hom,Het,WT\n")
        name = f"genotype_column_{self.line.pk}"
        table = draft_table(draft)
        form = GeneticsForm(draft, table)
        self.assertEqual(form[name].value(), "3")
        draft.configuration["bindings"][f"genotype:{self.line.pk}"] = binding(table["columns"][2])
        form = GeneticsForm(draft, table)
        self.assertEqual(form[name].value(), "2")
        data = self.form_data(form)
        data[name] = ""
        bound = GeneticsForm(draft, table, data=data)
        self.assertEqual(bound[name].value(), "")

    def test_duplicate_line_name_headers_require_manual_column_selection(self):
        draft = self.draft("Date,Mouse ID,Import line,IMPORT LINE,IL\n28.04.26,OFFICIAL-1,Hom,Het,WT\n")
        form = GeneticsForm(draft, draft_table(draft))
        self.assertEqual(form[f"genotype_column_{self.line.pk}"].value(), "")

    def test_crossing_suggestions_preserve_saved_choices_and_overrides(self):
        other = MouseLine.objects.create(species=self.species, name="Chosen crossing")
        draft = self.draft("Date,Mouse ID,Strain\n28.04.26,OFFICIAL-1,Import lnie\n")
        draft.configuration["crossing_map"] = {"Import lnie": other.crossings.get().pk}
        form = GeneticsForm(draft, draft_table(draft))
        name = form.crossing_rows[0]["name"]
        self.assertEqual(form[name].value(), other.crossings.get().pk)
        self.assertEqual(form["crossing_id"].value(), "")
        draft.configuration["crossing_map"]["Import lnie"] = ""
        self.assertEqual(GeneticsForm(draft, draft_table(draft))[name].value(), "")
        draft.configuration["defaults"]["crossing_id"] = self.crossing.pk
        self.assertEqual(GeneticsForm(draft, draft_table(draft))["crossing_id"].value(), self.crossing.pk)
        key = animal_groups(draft)[0]["key"]
        draft.state["animals"][key]["details"] = {"crossing_definition": other.crossings.get().pk}
        self.assertEqual(AnimalsForm(draft, draft_table(draft))[f"{key}_crossing_definition"].value(), other.crossings.get().pk)

    def test_genotype_mapping_manual_override_and_no_history(self):
        draft = self.draft("Date,Mouse ID,Genotype\n28.04.26,OFFICIAL-1,Hett\n")
        draft.configuration["bindings"][f"genotype:{self.line.pk}"] = binding(draft_table(draft)["columns"][2])
        self.assertTrue(build_review(draft)["errors"])
        draft.configuration["genotype_maps"] = {str(self.line.pk): {"Hett": "het"}}
        preview = self.reviewed(draft)
        commit_import(draft.pk, self.user, self.experiment, [c["token"] for c in preview["changes"]])
        self.assertEqual(self.mouse.genotype_calls.get().zygosity, "het")
        self.assertNotIn("genotype", self.experiment.recordings.get().values)
        draft = self.draft()
        next(iter(draft.state["animals"].values()))["genotypes"] = {str(self.line.pk): "hom"}
        preview = self.reviewed(draft)
        self.assertEqual(preview["animals"][0]["calls"], {str(self.line.pk): "hom"})

    def test_changed_mouse_or_schema_requires_refresh(self):
        draft = self.draft()
        self.reviewed(draft)
        Mouse.objects.filter(pk=self.mouse.pk).update(notes="changed elsewhere")
        with self.assertRaisesMessage(ValidationError, "changed after review"):
            commit_import(draft.pk, self.user, self.experiment, [])
        self.reviewed(draft)
        self.field.label = "New label"
        self.field.save()
        with self.assertRaisesMessage(ValidationError, "changed after review"):
            commit_import(draft.pk, self.user, self.experiment, [])

    def test_repeated_submit_expiry_and_owner(self):
        draft = self.draft()
        self.reviewed(draft)
        first = commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(commit_import(draft.pk, self.user, self.experiment, []), first)
        self.assertEqual(self.experiment.recordings.count(), 2)
        with self.assertRaises(SpreadsheetImportDraft.DoesNotExist):
            commit_import(draft.pk, self.other, self.experiment, [])
        draft = self.draft()
        draft.expires_at = timezone.now() - timedelta(seconds=1)
        draft.save()
        with self.assertRaisesMessage(ValidationError, "expired"):
            commit_import(draft.pk, self.user, self.experiment, [])
        call_command("cleanup_import_drafts", stdout=io.StringIO())
        self.assertFalse(SpreadsheetImportDraft.objects.filter(pk=draft.pk).exists())

    def test_recording_failure_rolls_back_created_animals_and_draft(self):
        draft = self.draft("Date,Mouse ID\n28.04.26,NEW-ID\n")
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk)
        self.reviewed(draft)
        with patch("lab.forms.recordings.RecordingForm.save", side_effect=ValidationError("forced failure")):
            with self.assertRaises(ValidationError):
                commit_import(draft.pk, self.user, self.experiment, [])
        self.assertFalse(Mouse.objects.filter(pk="NEW-ID").exists())
        draft.refresh_from_db()
        self.assertIsNone(draft.completed_at)

    def test_upload_and_all_wizard_pages(self):
        response = self.client.post(self.url(), {"spreadsheet": SimpleUploadedFile("data.csv", b"Date,Mouse ID\n28.04.26,OFFICIAL-1\n")})
        self.assertEqual(response.status_code, 302)
        draft = SpreadsheetImportDraft.objects.get()
        for step in ("source", "setup", "mapping", "animals", "genetics", "procedures", "review"):
            response = self.client.get(self.url(draft), {"step": step})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Import from spreadsheet")
        self.assertContains(self.client.get(reverse("lab:recordings_page", args=[self.project.pk, self.experiment.pk])), "Import from spreadsheet")

    def test_draft_routes_scope_owner_experiment_and_expiry(self):
        draft = self.draft()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url(draft)).status_code, 404)
        self.client.force_login(self.user)
        draft.expires_at = timezone.now() - timedelta(seconds=1)
        draft.save()
        self.assertEqual(self.client.get(self.url(draft)).status_code, 410)

    def test_review_post_with_individual_approval_and_stale_tab(self):
        draft = self.draft("Date,Mouse ID,Sex\n28.04.26,OFFICIAL-1,M\n")
        response = self.client.get(self.url(draft), {"step": "review"})
        preview = response.context["preview"]
        post = {"step": "review", "revision": response.context["revision"], "review_fingerprint": preview["fingerprint"], "approve": preview["changes"][0]["token"]}
        response = self.client.post(self.url(draft), post)
        self.assertEqual(response.status_code, 302)
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.sex, "M")
        draft = self.draft()
        response = self.client.post(self.url(draft), {"step": "source", "revision": "old"})
        self.assertContains(response, "changed in another tab")

    def test_genotyping_mixed_crossings_rejected(self):
        self.experiment.recording_type = RecordingType.objects.get(slug="genotyping")
        self.experiment.save()
        other_line = MouseLine.objects.create(species=self.species, name="Other genotype line")
        Mouse.objects.create(mouse_id="SECOND", protocol=self.protocol, crossing_definition=other_line.crossings.get())
        draft = self.draft("Date,Mouse ID\n28.04.26,OFFICIAL-1\n28.04.26,SECOND\n")
        draft.configuration["grouping"] = "date"
        self.assertTrue(any("one crossing" in e for e in build_review(draft)["errors"]))

    def test_full_wizard_round_trip_preserves_inputs_and_saves(self):
        response = self.client.post(self.url(), {"spreadsheet": SimpleUploadedFile("data.csv", b"Date,Mouse ID,Frames\n28.04.26,OFFICIAL-1,15\n")})
        draft = SpreadsheetImportDraft.objects.get()
        self.assertEqual(response.status_code, 302)
        destinations = {"source": "setup", "setup": "mapping", "mapping": "animals", "animals": "genetics", "genetics": "procedures", "procedures": "review"}
        for step, next_step in destinations.items():
            response = self.client.get(self.url(draft), {"step": step})
            form = response.context["form"]
            data = {name: form[name].value() for name in form.fields if form[name].value() is not None}
            data.update(step=step, revision=response.context["revision"], next_step=next_step)
            if step == "source":
                data["selected_rows"] = ["2"]
            if step == "setup":
                data.update(protocol_id=self.protocol.pk, species_id=self.species.pk, marking_type="tattoo")
            response = self.client.post(self.url(draft), data)
            self.assertEqual(response.status_code, 302, response.content.decode()[:1500])
            self.assertTrue(response.url.endswith("step=" + next_step))
        response = self.client.get(self.url(draft), {"step": "review"})
        self.assertEqual(response.context["preview"]["errors"], [])
        response = self.client.post(self.url(draft), {"step": "review", "revision": response.context["revision"], "review_fingerprint": response.context["preview"]["fingerprint"]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.experiment.recordings.get().values, {"frames": 15})
        draft.refresh_from_db()
        self.assertEqual(draft.configuration["defaults"]["protocol_id"], self.protocol.pk)
        self.assertEqual(draft.configuration["defaults"]["species_id"], self.species.pk)
        self.assertNotIn("marking_type", draft.configuration["defaults"])

    def test_import_settings_is_second_tab_and_owns_layout_and_shared_animal_details(self):
        draft = self.draft()
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, species_id=self.species.pk, marking_type="ear_clip")
        draft.save()
        response = self.client.get(self.url(draft), {"step": "setup"})
        self.assertEqual([tab["key"] for tab in response.context["tabs"]], ["source", "setup", "mapping", "animals", "genetics", "procedures", "review"])
        self.assertContains(response, "2. Import settings")
        self.assertContains(response, "Recording layout")
        self.assertContains(response, "Shared animal details")
        self.assertEqual(response.context["form"]["protocol_id"].value(), self.protocol.pk)
        self.assertNotIn("marking_type", response.context["form"].fields)
        for step in ("mapping", "animals"):
            response = self.client.get(self.url(draft), {"step": step})
            self.assertNotContains(response, "Recording layout")
            self.assertNotContains(response, "Shared animal details")
            self.assertNotIn("protocol_id", response.context["form"].fields)
            self.assertNotIn("date_order", response.context["form"].fields)

    def test_changing_identity_in_import_settings_invalidates_animal_resolutions_and_review(self):
        draft = self.draft("Date,Local identifier\n28.04.26,LE\n30.04.26,LE\n")
        previous_bindings = dict(draft.configuration["bindings"])
        draft.state["review_fingerprint"] = "previous review"
        draft.save()
        response = self.client.get(self.url(draft), {"step": "setup"})
        form = response.context["form"]
        data = {name: form[name].value() if form[name].value() is not None else "" for name in form.fields}
        data.update(step="setup", next_step="mapping", revision=response.context["revision"], identity_columns=["0"], protocol_id=self.protocol.pk, date_order="MDY", grouping="date")
        response = self.client.post(self.url(draft), data)
        self.assertRedirects(response, self.url(draft) + "?step=mapping")
        draft.refresh_from_db()
        self.assertNotIn("animals", draft.state)
        self.assertNotIn("review_fingerprint", draft.state)
        self.assertEqual(draft.configuration["bindings"], previous_bindings)
        self.assertEqual(draft.configuration["date_order"], "MDY")
        self.assertEqual(draft.configuration["grouping"], "date")
        self.assertEqual(draft.configuration["defaults"]["protocol_id"], self.protocol.pk)
        self.assertEqual(len(animal_groups(draft)), 2)

    def test_invalid_import_settings_retain_inputs_without_saving(self):
        draft = self.draft()
        original = json.loads(json.dumps(draft.configuration))
        response = self.client.get(self.url(draft), {"step": "setup"})
        data = {"step": "setup", "revision": response.context["revision"], "date_order": "MDY", "grouping": "date", "protocol_id": "999999", "marking_type": "ear_clip"}
        response = self.client.post(self.url(draft), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("protocol_id", response.context["form"].errors)
        self.assertEqual(response.context["form"]["date_order"].value(), "MDY")
        self.assertNotIn("marking_type", response.context["form"].fields)
        draft.refresh_from_db()
        self.assertEqual(draft.configuration, original)

    def test_import_complete_imaging_example(self):
        path = Path(__file__).resolve().parents[2] / "docs" / "Alpha syn Myo15_iAchSnFr.xlsx"
        with path.open("rb") as file:
            source = read_upload(file)
        table = table_from_sheet(source[0], 1)
        draft = self.draft()
        draft.source = source
        draft.configuration = new_definition(self.experiment, table["columns"])
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk)
        draft.state = {"sheet": source[0]["name"], "header_row": 1, "selected_rows": [r["number"] for r in table["rows"]]}
        draft.state["animals"] = {g["key"]: initial_resolution(g, draft.configuration) for g in animal_groups(draft)}
        preview = self.reviewed(draft)
        self.assertEqual(len(preview["animals"]), 12)
        self.assertEqual(len(preview["records"]), 50)
        result = commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(result["recordings"], 50)
        self.assertEqual(result["created_animals"], 12)

    def test_genotyping_example_combines_eight_animals_and_keeps_calls_on_animals(self):
        self.experiment.recording_type = RecordingType.objects.get(slug="genotyping")
        self.experiment.save()
        path = Path(__file__).resolve().parents[2] / "docs" / "EAR CLIP SHEET Gria2 x GCaMP6f x NeuroD1-cre 16.10.26.xlsx"
        with path.open("rb") as file:
            source = read_upload(file)
        table = table_from_sheet(source[0], 1)
        draft = self.draft()
        draft.source = source
        draft.configuration = new_definition(self.experiment, table["columns"])
        draft.configuration["bindings"]["animal:identifier"] = binding(table["columns"][1])
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk, recording_date="2026-10-16", **{f"genotype:{self.line.pk}": "het"})
        draft.configuration["grouping"] = "date"
        draft.state = {"sheet": source[0]["name"], "header_row": 1, "selected_rows": [r["number"] for r in table["rows"] if not r["flag"]]}
        draft.state["animals"] = {g["key"]: initial_resolution(g, draft.configuration) for g in animal_groups(draft)}
        self.reviewed(draft)
        result = commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(result["created_animals"], 8)
        self.assertEqual(result["recordings"], 1)
        record = self.experiment.recordings.get()
        self.assertEqual(record.mice.count(), 8)
        self.assertEqual(record.values, {"crossing": self.crossing.display_name})
        self.assertEqual(set(MouseGenotype.objects.filter(mouse__in=record.mice.all()).values_list("zygosity", flat=True)), {"het"})

    def test_equivalent_birth_dates_sexes_and_genotypes_are_consistent(self):
        draft = self.draft("Date,Mouse ID,Birth Date,Sex,Genotype\n28.04.26,OFFICIAL-1,01.01.26,Male,Het\n28.04.26,OFFICIAL-1,1.1.26,M,het\n")
        draft.configuration["bindings"][f"genotype:{self.line.pk}"] = binding(draft_table(draft)["columns"][4])
        preview = build_review(draft)
        self.assertEqual(preview["errors"], [])
        self.assertEqual(preview["animals"][0]["calls"], {str(self.line.pk): "het"})

    def test_duplicate_new_temporary_animal_identity_is_rejected_in_review(self):
        draft = self.draft("Date,Local identifier,Birth Date,Breeding pair\n28.04.26,LE,1.1.26,PAIR\n30.04.26,LE,1.1.26,PAIR\n")
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk)
        draft.configuration["identity_columns"] = [binding(draft_table(draft)["columns"][0])]
        draft.state["animals"] = {g["key"]: initial_resolution(g, draft.configuration) for g in animal_groups(draft)}
        preview = build_review(draft)
        self.assertTrue(any("Two new animals" in e for e in preview["errors"]))

    def test_individual_override_can_replace_conflicting_source_values(self):
        draft = self.draft("Date,Mouse ID,Sex\n28.04.26,OFFICIAL-1,M\n28.04.26,OFFICIAL-1,F\n")
        self.assertTrue(build_review(draft)["errors"])
        next(iter(draft.state["animals"].values()))["details"] = {"sex": "M"}
        self.assertEqual(build_review(draft)["errors"], [])

    def test_genetics_individual_controls_only_include_the_animals_lines(self):
        other_line = MouseLine.objects.create(species=self.species, name="Other individual line")
        Mouse.objects.create(mouse_id="OTHER", protocol=self.protocol, crossing_definition=other_line.crossings.get())
        draft = self.draft("Date,Mouse ID\n28.04.26,OFFICIAL-1\n28.04.26,OTHER\n")
        form = GeneticsForm(draft, draft_table(draft))
        controls = {row["group"]["identifier"]: [f["line_id"] for f in row["fields"]] for row in form.individual_rows}
        self.assertEqual(controls, {"OFFICIAL-1": [str(self.line.pk)], "OTHER": [str(other_line.pk)]})

    def test_genotype_columns_follow_crossing_order_without_changing_saved_bindings(self):
        lines = [MouseLine.objects.create(species=self.species, name=name) for name in ("Gria2", "GCaMP6f", "NeuroDCre")]
        crossing, _ = resolve_crossing(self.species, lines)
        draft = self.draft("Date,Mouse ID,First,Second,Third\n28.04.26,OFFICIAL-1,Het,Hom,WT\n")
        draft.configuration["defaults"]["crossing_id"] = crossing.pk
        columns = draft_table(draft)["columns"]
        for index, line in enumerate(lines, 2):
            draft.configuration["bindings"][f"genotype:{line.pk}"] = binding(columns[index])
        draft.save()
        for expected in (lines, [lines[2], lines[0], lines[1]]):
            for position, line in enumerate(expected):
                CrossingLine.objects.filter(crossing=crossing, line=line).update(position=position)
            response = self.client.get(self.url(draft), {"step": "genetics"})
            form = response.context["form"]
            self.assertEqual([row["line"] for row in form.line_rows], crossing.ordered_lines())
            self.assertEqual([item["line_id"] for item in form.individual_rows[0]["fields"]], [str(line.pk) for line in expected])
            markup = Markup(response.content.decode())
            labels = [node.text.strip() for node in markup.find("th", scope="row") if node.within("table", **{"class": "data-table procedure-matrix import-mapping-table"})]
            self.assertEqual(labels, [line.name for line in expected])
            for index, line in enumerate(lines, 2):
                self.assertEqual(form[f"genotype_column_{line.pk}"].value(), str(index))

    def test_multiple_crossings_keep_first_seen_mapping_order_and_each_animals_own_line_order(self):
        z, a, extra = [MouseLine.objects.create(species=self.species, name=name) for name in ("Z line", "A line", "Extra line")]
        first, _ = resolve_crossing(self.species, [z, a])
        second, _ = resolve_crossing(self.species, [a, z, extra])
        draft = self.draft("Date,Mouse ID,Strain\n28.04.26,NEW-1,First crossing\n28.04.26,NEW-2,Second crossing\n")
        draft.configuration["crossing_map"] = {"First crossing": first.pk, "Second crossing": second.pk}
        form = GeneticsForm(draft, draft_table(draft))
        self.assertEqual([row["line"] for row in form.line_rows], [z, a, extra])
        self.assertEqual([[item["line_id"] for item in row["fields"]] for row in form.individual_rows], [[str(line.pk) for line in first.ordered_lines()], [str(line.pk) for line in second.ordered_lines()]])

    def test_deleted_individual_protocol_is_reported_before_submission(self):
        protocol = Protocol.objects.create(protocol_number=991, licence_reference="IMPORT", name="Unused protocol")
        draft = self.draft()
        next(iter(draft.state["animals"].values()))["details"] = {"protocol": protocol.pk}
        protocol.delete()
        self.assertTrue(any("protocol is no longer available" in e for e in build_review(draft)["errors"]))

    def genotype_draft(self, text):
        other = MouseLine.objects.create(species=self.species, name="Second genotype line")
        crossing, _ = resolve_crossing(self.species, [self.line, other])
        Mouse.objects.filter(pk=self.mouse.pk).update(crossing_definition=crossing)
        draft = self.draft(text)
        columns = draft_table(draft)["columns"]
        draft.configuration["bindings"].update({
            f"genotype:{self.line.pk}": binding(columns[2]),
            f"genotype:{other.pk}": binding(columns[3]),
        })
        draft.save()
        return draft, other

    def test_recognized_genotype_terms_are_not_shown_for_resolution(self):
        draft, other = self.genotype_draft("Date,Mouse ID,First,Second\n28.04.26,OFFICIAL-1,Het,HOM\n28.04.26,OFFICIAL-1,HET,Hom\n")
        response = self.client.get(self.url(draft), {"step": "genetics"})
        self.assertEqual(response.context["form"].term_rows, [])
        self.assertNotContains(response, "import-unknown-terms")
        preview = build_review(draft)
        self.assertEqual(preview["errors"], [])
        self.assertEqual(preview["animals"][0]["calls"], {str(self.line.pk): "het", str(other.pk): "hom"})

    def test_system_category_matches_all_capitalization_variants(self):
        GenotypeCategory.objects.get(code="cre")
        draft, _ = self.genotype_draft("Date,Mouse ID,First,Second\n28.04.26,OFFICIAL-1,CRE,cre\n28.04.26,OFFICIAL-1,Cre,CRE\n")
        self.assertEqual(GeneticsForm(draft, draft_table(draft)).term_rows, [])
        preview = build_review(draft)
        self.assertEqual(preview["errors"], [])
        self.assertEqual(set(preview["animals"][0]["calls"].values()), {"cre"})

    def test_unknown_term_resolves_once_across_lines_and_stays_resolved_after_saving(self):
        draft, other = self.genotype_draft("Date,Mouse ID,First,Second\n28.04.26,OFFICIAL-1,ODDCALL,oddcall\n28.04.26,OFFICIAL-1,OddCall,ODDCALL\n")
        response = self.client.get(self.url(draft), {"step": "genetics"})
        form = response.context["form"]
        self.assertEqual(len(form.term_rows), 1)
        self.assertEqual(set(form.term_rows[0]["variants"]), {"ODDCALL", "OddCall", "oddcall"})
        markup = Markup(response.content.decode())
        unresolved = next(node for node in markup.find("details") if "import-unknown-terms" in node.attrs.get("class", ""))
        self.assertIn("open", unresolved.attrs)
        for cycle in range(2):
            form = response.context["form"]
            data = {name: form[name].value() if form[name].value() is not None else "" for name in form.fields}
            if cycle == 0:
                data[form.term_rows[0]["name"]] = "het"
            else:
                self.assertEqual(form.term_rows, [])
            data.update(step="genetics", next_step="genetics", revision=response.context["revision"])
            response = self.client.post(self.url(draft), data, follow=True)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "import-unknown-terms")
            markup = Markup(response.content.decode())
            individual = next(node for node in markup.find("details") if "import-individual-genotypes" in node.attrs.get("class", ""))
            self.assertNotIn("open", individual.attrs)
        draft.refresh_from_db()
        self.assertEqual(draft.configuration["genotype_terms"], {"oddcall": "het"})
        preview = self.reviewed(draft)
        self.assertEqual(preview["animals"][0]["calls"], {str(self.line.pk): "het", str(other.pk): "het"})
        commit_import(draft.pk, self.user, self.experiment, [entry["token"] for entry in preview["changes"]])
        self.assertEqual(set(self.mouse.genotype_calls.values_list("zygosity", flat=True)), {"het"})

    def test_existing_line_maps_are_case_insensitive_and_not_erased_when_hidden(self):
        draft, other = self.genotype_draft("Date,Mouse ID,First,Second\n28.04.26,OFFICIAL-1,HETT,hett\n")
        draft.configuration["genotype_maps"] = {str(self.line.pk): {"Hett": "het"}, str(other.pk): {"hett": "het"}}
        form = GeneticsForm(draft, draft_table(draft))
        self.assertEqual(form.term_rows, [])
        data = {name: form[name].value() if form[name].value() is not None else "" for name in form.fields}
        bound = GeneticsForm(draft, draft_table(draft), data=data)
        self.assertTrue(bound.is_valid(), bound.errors)
        bound.apply(draft)
        self.assertEqual(draft.configuration["genotype_maps"], {str(self.line.pk): {"Hett": "het"}, str(other.pk): {"hett": "het"}})
        self.assertEqual(build_review(draft)["errors"], [])

    def test_unknown_terms_must_be_defined_before_review(self):
        draft, _ = self.genotype_draft("Date,Mouse ID,First,Second\n28.04.26,OFFICIAL-1,Hett,HETT\n")
        form = GeneticsForm(draft, draft_table(draft))
        self.assertEqual(len(form.term_rows), 1)
        data = {name: form[name].value() if form[name].value() is not None else "" for name in form.fields}
        data[form.term_rows[0]["name"]] = ""
        data["next_step"] = "review"
        bound = GeneticsForm(draft, draft_table(draft), data=data)
        self.assertFalse(bound.is_valid())
        self.assertIn(form.term_rows[0]["name"], bound.errors)

    def test_fuzzy_terms_still_need_confirmation_and_whitespace_is_empty(self):
        draft, _ = self.genotype_draft("Date,Mouse ID,First,Second\n28.04.26,OFFICIAL-1,Het?, \n")
        form = GeneticsForm(draft, draft_table(draft))
        self.assertEqual([row["key"] for row in form.term_rows], ["het?"])
        self.assertTrue(build_review(draft)["errors"])
        draft.configuration["genotype_terms"] = {"HET?": "het"}
        self.assertEqual(build_review(draft)["errors"], [])
        self.assertEqual(GeneticsForm(draft, draft_table(draft)).term_rows, [])

    def test_individual_overrides_are_collapsed_on_get_and_after_saves(self):
        draft, _ = self.genotype_draft("Date,Mouse ID,First,Second\n28.04.26,OFFICIAL-1,Het,Hom\n")
        response = self.client.get(self.url(draft), {"step": "genetics"})
        for _ in range(2):
            markup = Markup(response.content.decode())
            individual = next(node for node in markup.find("details") if "import-individual-genotypes" in node.attrs.get("class", ""))
            self.assertNotIn("open", individual.attrs)
            form = response.context["form"]
            data = {name: form[name].value() if form[name].value() is not None else "" for name in form.fields}
            data.update(step="genetics", next_step="genetics", revision=response.context["revision"])
            response = self.client.post(self.url(draft), data, follow=True)
            self.assertEqual(response.status_code, 200)

    def add_shared_value_fields(self):
        for key, data_type, required, choices in (
            ("temperature", "float", False, []),
            ("acquired", "date", False, []),
            ("flag", "boolean", True, []),
            ("mode", "choice", True, ["high", "low"]),
        ):
            RecordingField.objects.create(recording_type=self.recording_type, key=key, label=key.title(), data_type=data_type, required=required, choices=choices)

    def test_sequence_is_only_offered_as_a_configured_recording_field(self):
        draft = self.draft()
        response = self.client.get(self.url(draft), {"step": "mapping"})
        self.assertNotIn("column_sequence_number", response.context["form"].fields)
        self.assertNotIn("column_field:sequence_number", response.context["form"].fields)
        response = self.client.get(self.url(draft), {"step": "review"})
        self.assertNotContains(response, "Sequence:")
        self.assertNotContains(response, "Sequence</th>")

    def test_configurable_sequence_import_maps_blanks_groups_and_reuses_bindings(self):
        field = RecordingField.objects.create(recording_type=self.recording_type, key="sequence_number", label="Sequence", data_type="integer")
        draft = self.draft("Date,Mouse ID,Sequence\n28.04.26,OFFICIAL-1,7\n28.04.26,OFFICIAL-1,\n")
        self.assertIn("field:sequence_number", draft.configuration["bindings"])
        self.assertNotIn("sequence_number", draft.configuration["defaults"])
        response = self.client.get(self.url(draft), {"step": "mapping"})
        rows = response.context["form"].mapping_rows
        sequence = next(row for row in rows if row["column"].name == "column_field:sequence_number")
        self.assertFalse(sequence["required"])
        self.assertEqual([record["values"] for record in build_review(draft)["records"]], [{"sequence_number": 7}, {}])
        draft.configuration["grouping"] = "date"
        preview = self.reviewed(draft)
        self.assertEqual(preview["records"][0]["values"], {"sequence_number": 7})
        response = self.client.get(self.url(draft), {"step": "review"})
        self.assertContains(response, "Sequence: 7", count=1)
        commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(self.experiment.recordings.get().values, {"sequence_number": 7})

        second = self.draft("Sequence,Mouse ID,Date\n9,OFFICIAL-1,29.04.26\n")
        second.configuration = json.loads(json.dumps(draft.configuration))
        self.assertEqual(build_review(second)["records"][0]["values"], {"sequence_number": 9})
        field.label = "Run order"
        field.save()
        self.assertEqual(build_review(second)["records"][0]["display_values"], [("Run order", 9)])

    def test_configurable_sequence_group_conflicts_and_invalid_values_are_reported(self):
        RecordingField.objects.create(recording_type=self.recording_type, key="sequence_number", label="Sequence", data_type="integer")
        draft = self.draft("Date,Mouse ID,Sequence\n28.04.26,OFFICIAL-1,7\n28.04.26,OFFICIAL-1,8\n")
        draft.configuration["grouping"] = "date"
        errors = build_review(draft)["errors"]
        self.assertTrue(any("Conflicting values" in error and "Sequence [C]" in error for error in errors), errors)
        invalid = self.draft("Date,Mouse ID,Sequence\n28.04.26,OFFICIAL-1,wrong\n")
        errors = build_review(invalid)["errors"]
        self.assertTrue(any("Sequence [C]" in error for error in errors), errors)

    def test_mapping_controls_follow_field_types_and_required_rows_come_first(self):
        self.add_shared_value_fields()
        draft = self.draft()
        draft.configuration["defaults"].update(recording_date="28.04.26", **{"field:acquired": "27.04.26", "field:flag": False, "field:mode": "HIGH", "animal:sex": "Female", "animal:status": "alive"})
        draft.save()
        response = self.client.get(self.url(draft), {"step": "mapping"})
        form = response.context["form"]
        markup = Markup(response.content.decode())
        for name, input_type in (
            ("recording_date", "date"), ("animal:date_of_birth", "date"), ("field:acquired", "date"),
            ("field:frames", "number"), ("field:temperature", "number"),
        ):
            node = markup.find("input", name=f"default_{name}")[0]
            self.assertEqual(node.attrs["type"], input_type)
            self.assertNotIn("required", node.attrs)
        self.assertEqual(markup.find("input", name="default_recording_date")[0].attrs["value"], "2026-04-28")
        self.assertEqual(markup.find("input", name="default_field:acquired")[0].attrs["value"], "2026-04-27")
        for key in ("animal:status", "animal:sex", "field:mode", "field:flag"):
            self.assertEqual(len(markup.find("select", name=f"default_{key}")), 1)
        self.assertEqual(form["default_field:flag"].value(), "false")
        self.assertEqual(form["default_field:mode"].value(), "high")
        self.assertEqual(form["default_animal:sex"].value(), "F")
        self.assertEqual(form["default_animal:status"].value(), "Alive")
        self.assertEqual(list(form.fields["default_field:mode"].choices)[0][0], "")
        required = [row["column"].name for row in form.mapping_rows if row["required"]]
        self.assertCountEqual(required, ["column_recording_date", "column_field:flag", "column_field:mode"])
        priorities = [2 if row["associated"] else 0 if row["required"] else 1 for row in form.mapping_rows]
        self.assertEqual(priorities, sorted(priorities))

    def test_shared_typed_values_remain_json_serializable_and_apply_to_recordings(self):
        self.add_shared_value_fields()
        draft = self.draft("Date,Mouse ID\n28.04.26,OFFICIAL-1\n")
        initial = MappingForm(draft, draft_table(draft))
        data = {name: initial[name].value() if initial[name].value() is not None else "" for name in initial.fields}
        data["identity_columns"] = []
        data.update({"default_field:frames": "12", "default_field:temperature": "23.5", "default_field:acquired": "2026-04-27", "default_field:flag": "false", "default_field:mode": "high", "default_animal:status": "Alive"})
        for key in ("field:frames", "field:temperature", "field:acquired", "field:flag", "field:mode", "animal:status"):
            data[f"mode_{key}"] = "shared"
        bound = MappingForm(draft, draft_table(draft), data=data)
        self.assertTrue(bound.is_valid(), bound.errors)
        draft.configuration = json.loads(json.dumps(bound.definition()))
        self.assertIs(draft.configuration["defaults"]["field:flag"], False)
        self.assertEqual(draft.configuration["defaults"]["animal:date_of_birth"], "")
        self.reviewed(draft)
        commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(self.experiment.recordings.get().values, {"frames": 12, "temperature": 23.5, "acquired": "2026-04-27", "flag": False, "mode": "high"})
        # The persisted No selection must reappear as No, while an unset shared
        # boolean must stay absent rather than replacing source cells with False.
        restored = MappingForm(draft, draft_table(draft))
        self.assertEqual(restored["default_field:flag"].value(), "false")
        data["default_field:flag"] = ""
        bound = MappingForm(draft, draft_table(draft), data=data)
        self.assertTrue(bound.is_valid(), bound.errors)
        self.assertEqual(bound.definition()["defaults"]["field:flag"], "")

    def test_invalid_shared_values_have_field_errors_before_saving_mapping(self):
        self.add_shared_value_fields()
        draft = self.draft()
        initial = MappingForm(draft, draft_table(draft))
        data = {name: initial[name].value() if initial[name].value() is not None else "" for name in initial.fields}
        data["identity_columns"] = []
        for key, invalid in (("field:frames", "1.5"), ("field:temperature", "warm"), ("field:acquired", "2026-02-30"), ("recording_date", "invalid"), ("field:mode", "unavailable"), ("field:flag", "maybe"), ("animal:status", "unavailable"), ("animal:sex", "unavailable")):
            with self.subTest(key=key):
                name = f"default_{key}"
                bound = MappingForm(draft, draft_table(draft), data={**data, name: invalid, f"mode_{key}": "shared"})
                self.assertFalse(bound.is_valid())
                self.assertIn(name, bound.errors)

    def test_shared_crossing_override_is_collapsed_below_source_strain_mappings(self):
        draft = self.draft("Date,Mouse ID,Strain\n28.04.26,OFFICIAL-1,Import line\n")
        draft.configuration["defaults"]["crossing_id"] = self.crossing.pk
        draft.save()
        response = self.client.get(self.url(draft), {"step": "genetics"})
        markup = Markup(response.content.decode())
        panel = next(node for node in markup.find("details") if "import-shared-crossing" in node.attrs.get("class", ""))
        self.assertNotIn("open", panel.attrs)
        source_table = next(node for node in markup.find("table") if "import-crossing-table" in node.attrs.get("class", ""))
        self.assertLess(markup.elements.index(source_table), markup.elements.index(panel))
        self.assertIn("· set", panel.text)
        self.assertEqual(response.context["form"]["crossing_id"].value(), self.crossing.pk)

    def test_mapping_source_choices_are_exclusive_and_do_not_validate_inactive_inputs(self):
        draft = self.draft("Date,Mouse ID,Frames\n28.04.26,OFFICIAL-1,10\n28.04.26,OFFICIAL-1,\n")
        draft.configuration["defaults"]["field:frames"] = 99  # A draft using the previous fallback UI.
        initial = MappingForm(draft, draft_table(draft))
        self.assertEqual(initial["mode_field:frames"].value(), "column")
        data = {name: initial[name].value() if initial[name].value() is not None else "" for name in initial.fields}
        data["identity_columns"] = []
        data["default_field:frames"] = "inactive invalid number"
        column = MappingForm(draft, draft_table(draft), data=data)
        self.assertTrue(column.is_valid(), column.errors)
        draft.configuration = column.definition()
        self.assertIn("field:frames", draft.configuration["bindings"])
        self.assertEqual(draft.configuration["defaults"]["field:frames"], "")
        preview = build_review(draft)
        self.assertEqual(preview["errors"], [])
        self.assertEqual([record["values"] for record in preview["records"]], [{"frames": 10}, {}])
        data.update({"mode_field:frames": "shared", "default_field:frames": "0", "column_field:frames": "invalid inactive column"})
        shared = MappingForm(draft, draft_table(draft), data=data)
        self.assertTrue(shared.is_valid(), shared.errors)
        draft.configuration = json.loads(json.dumps(shared.definition()))
        self.assertNotIn("field:frames", draft.configuration["bindings"])
        self.assertEqual(draft.configuration["defaults"]["field:frames"], 0)
        self.assertEqual([record["values"] for record in build_review(draft)["records"]], [{"frames": 0}, {"frames": 0}])
        self.assertEqual(MappingForm(draft, draft_table(draft))["mode_field:frames"].value(), "shared")
        self.assertFalse(any(key.startswith("mode_") for key in draft.configuration))

    def test_animal_creation_requirements_remain_visible_with_association_ordering(self):
        draft = self.draft()
        response = self.client.get(self.url(draft), {"step": "mapping"})
        rows = response.context["form"].mapping_rows
        conditional = {row["column"].name: row for row in rows if row["requirement"]}
        self.assertCountEqual(conditional, ["column_animal:identifier", "column_animal:crossing", "column_animal:date_of_birth", "column_animal:breeding_pair", "column_animal:tattoo"])
        priority = [2 if row["associated"] else 0 if row["required"] else 1 for row in rows]
        self.assertEqual(priority, sorted(priority))
        self.assertContains(response, "Choose one per field:")
        self.assertNotContains(response, "Creating new animals?")
        self.assertContains(response, "Required when generating animal IDs.")
        markup = Markup(response.content.decode())
        for row in rows:
            modes = markup.find("input", name=row["mode"].name)
            self.assertEqual(len(modes), 1)
            self.assertEqual(modes[0].attrs["type"], "hidden")
        self.assertEqual(markup.find("input", type="radio"), [])

    def procedure_entry(self, **changes):
        kind, _ = ProcedureType.objects.get_or_create(name="Import test procedure")
        return {"procedure_type": kind.pk, "date": "2026-10-21", "lab_member": "AB", "lab_book_page": "001", **changes}

    def test_penultimate_step_reuses_add_animals_procedure_editor_without_nested_forms(self):
        draft = self.draft()
        response = self.client.get(self.url(draft), {"step": "procedures"})
        self.assertContains(response, "6. Add procedures")
        self.assertContains(response, "Procedures for this import")
        self.assertContains(response, "lab/litter_procedures.js")
        markup = Markup(response.content.decode())
        editor = markup.find("form", id="litter-procedure-form")[0]
        self.assertFalse(editor.within("form", **{"data-import-form": None}))
        self.assertEqual(editor.attrs["data-procedure-scope"], "import")
        queue = markup.find("input", name="procedure_drafts")[0]
        self.assertTrue(queue.within("form", **{"data-import-form": None}))
        self.assertEqual(json.loads(queue.attrs["value"]), [])

    def test_procedure_queue_saves_without_writes_then_imports_once_per_animal(self):
        draft = self.draft()
        entries = [self.procedure_entry(), self.procedure_entry(date="", lab_member="CD")]
        response = self.client.get(self.url(draft), {"step": "procedures"})
        response = self.client.post(self.url(draft), {"step": "procedures", "revision": response.context["revision"], "procedure_drafts": json.dumps(entries)})
        self.assertRedirects(response, self.url(draft) + "?step=review")
        self.assertFalse(Procedure.objects.exists())
        self.assertFalse(Recording.objects.exists())
        draft.refresh_from_db()
        self.assertEqual(draft.configuration["procedure_drafts"], entries)
        preview = self.reviewed(draft)
        self.assertEqual(len(preview["procedures"]), 2)  # Repeated imaging rows resolve one animal.
        review = self.client.get(self.url(draft), {"step": "review"})
        self.assertContains(review, "Procedures to add")
        result = commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(result["recordings"], 2)
        self.assertEqual(result["procedures"], 2)
        rows = list(self.mouse.procedures.order_by("pk"))
        self.assertEqual(rows[0].date, date(2026, 10, 21))
        self.assertEqual(rows[0].lab_book_page, "001")
        self.assertIsNone(rows[1].date)
        self.assertEqual(rows[1].lab_member, "CD")
        self.assertEqual(commit_import(draft.pk, self.user, self.experiment, []), result)
        self.assertEqual(Procedure.objects.count(), 2)

    def test_procedure_editor_enabled_only_for_selected_allowed_protocol(self):
        draft = self.draft()
        restricted = Protocol.objects.create(protocol_number=991, licence_reference="IMPORT", name="Restricted", allows_regulated_procedures=False)
        for protocol_id, enabled in [("", False), (restricted.pk, False), (self.protocol.pk, True), (999999, False)]:
            with self.subTest(protocol_id=protocol_id):
                draft.configuration["defaults"]["protocol_id"] = protocol_id
                draft.save()
                response = self.client.get(self.url(draft), {"step": "procedures"})
                self.assertEqual(response.status_code, 200)
                markup = Markup(response.content.decode())
                fields = markup.find("fieldset", **{"data-procedure-fieldset": None})[0]
                help_text = markup.find("p", **{"data-procedure-disabled-help": None})[0]
                self.assertEqual("disabled" not in fields.attrs, enabled)
                self.assertEqual("hidden" in help_text.attrs, enabled)
                self.assertContains(response, 'href="?step=setup"')

        # Permissions are read again when returning to the tab.
        draft.configuration["defaults"]["protocol_id"] = self.protocol.pk
        draft.save()
        Protocol.objects.filter(pk=self.protocol.pk).update(allows_regulated_procedures=False)
        response = self.client.get(self.url(draft), {"step": "procedures"})
        self.assertFalse(response.context["procedure_fields_enabled"])

    def test_procedures_attach_to_new_and_existing_animals(self):
        draft = self.draft("Date,Mouse ID\n28.04.26,OFFICIAL-1\n28.04.26,NEW-ID\n")
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk)
        draft.configuration["procedure_drafts"] = [self.procedure_entry()]
        preview = self.reviewed(draft)
        self.assertEqual({row["mouse_id"] for row in preview["procedures"]}, {"OFFICIAL-1", "NEW-ID"})
        result = commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(result["created_animals"], 1)
        self.assertEqual(result["procedures"], 2)
        self.assertEqual(set(Procedure.objects.values_list("mouse_id", flat=True)), {"OFFICIAL-1", "NEW-ID"})

    def test_separate_identifiers_linked_to_same_animal_do_not_duplicate_procedures(self):
        draft = self.draft("Date,Mouse ID\n28.04.26,OFFICIAL-1\n30.04.26,OFFICIAL-1\n")
        draft.configuration["identity_columns"] = [binding(draft_table(draft)["columns"][0])]
        draft.state["animals"] = {group["key"]: initial_resolution(group, draft.configuration) for group in animal_groups(draft)}
        draft.configuration["procedure_drafts"] = [self.procedure_entry()]
        self.assertEqual(len(self.reviewed(draft)["procedures"]), 1)
        response = self.client.get(self.url(draft), {"step": "review"})
        markup = Markup(response.content.decode())
        self.assertEqual(len(markup.find("ul", **{"class": "import-review-procedures"})), 1)
        self.assertEqual(sum(len(animal["procedures"]) for animal in response.context["review_animals"]), 1)
        commit_import(draft.pk, self.user, self.experiment, [])
        self.assertEqual(Procedure.objects.count(), 1)

    def test_review_shows_procedures_with_each_animals_details_and_change_approvals(self):
        draft = self.draft("Date,Mouse ID,Sex\n28.04.26,OFFICIAL-1,Female\n28.04.26,NEW-ID,Female\n")
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk)
        draft.configuration["procedure_drafts"] = [self.procedure_entry(), self.procedure_entry(date="", lab_member="CD", lab_book_page="")]
        preview = self.reviewed(draft)
        response = self.client.get(self.url(draft), {"step": "review"})
        markup = Markup(response.content.decode())
        table_class = "data-table procedure-matrix import-review-table import-review-with-procedures"
        table = markup.find("table", **{"class": table_class})[0]
        self.assertIn("Procedures to add", table.text)
        self.assertNotContains(response, "<h2>Procedures to add</h2>")
        animal_rows = [row for row in markup.find("tr") if row.within("tbody") and row.within("table", **{"class": table_class})]
        self.assertEqual(len(animal_rows), 2)
        for row, animal in zip(animal_rows, preview["animals"]):
            self.assertIn(animal["mouse_id"], row.text)
            self.assertIn("Link" if animal["existing"] else "Create", row.text)
            self.assertEqual(row.text.count("Import test procedure"), 2)
            for detail in ("2026-10-21", "AB", "001", "CD", "—"):
                self.assertIn(detail, row.text)
            if animal["existing"]:
                self.assertIn("Apply Sex", row.text)
            else:
                self.assertIn("Female", row.text)
        approvals = markup.find("input", name="approve")
        self.assertCountEqual([field.attrs["value"] for field in approvals], [change["token"] for change in preview["changes"]])
        self.assertTrue(all(field.within("table", **{"class": table_class}) for field in approvals))

    def test_invalid_procedure_queue_preserves_draft_and_creates_nothing(self):
        draft = self.draft()
        valid = self.procedure_entry()
        invalids = ["text", [None], [{**valid, "procedure_type": 999999}], [{**valid, "procedure_type": True}], [{**valid, "date": "bad"}], [{**valid, "lab_member": []}], [{**valid, "lab_book_page": "x" * 51}], [valid] * 101]
        for entries in invalids:
            with self.subTest(entries=str(entries)[:80]):
                page = self.client.get(self.url(draft), {"step": "procedures"})
                response = self.client.post(self.url(draft), {"step": "procedures", "revision": page.context["revision"], "procedure_drafts": json.dumps(entries)})
                self.assertEqual(response.status_code, 200)
                self.assertIn("procedure_drafts", response.context["form"].errors)
                draft.refresh_from_db()
                self.assertEqual(draft.configuration["procedure_drafts"], [])
                self.assertFalse(Procedure.objects.exists())
                self.assertFalse(Recording.objects.exists())

    def test_protocol_restrictions_and_approved_protocol_changes_are_checked_for_procedures(self):
        restricted = Protocol.objects.create(protocol_number=991, licence_reference="IMPORT", name="Restricted", allows_regulated_procedures=False)
        Mouse.objects.filter(pk=self.mouse.pk).update(protocol=restricted)
        draft = self.draft()
        draft.configuration["procedure_drafts"] = [self.procedure_entry()]
        self.assertTrue(any("does not allow regulated procedures" in error for error in build_review(draft)["errors"]))
        draft.configuration["defaults"]["protocol_id"] = self.protocol.pk
        preview = self.reviewed(draft)
        token = next(change["token"] for change in preview["changes"] if change["field"] == "protocol")
        with self.assertRaisesMessage(ValidationError, "does not allow regulated procedures"):
            commit_import(draft.pk, self.user, self.experiment, [])
        self.assertFalse(Procedure.objects.exists())
        self.assertFalse(Recording.objects.exists())
        result = commit_import(draft.pk, self.user, self.experiment, [token])
        self.assertEqual(result["procedures"], 1)
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.protocol_id, self.protocol.pk)

    def test_procedure_type_references_are_validated_and_changes_require_new_review(self):
        draft = self.draft()
        entry = self.procedure_entry()
        draft.configuration["procedure_drafts"] = [entry]
        self.reviewed(draft)
        ProcedureType.objects.filter(pk=entry["procedure_type"]).update(name="Renamed import procedure")
        with self.assertRaisesMessage(ValidationError, "changed after review"):
            commit_import(draft.pk, self.user, self.experiment, [])
        ProcedureType.objects.filter(pk=entry["procedure_type"]).delete()
        with self.assertRaises(ValidationError):
            validate_definition(draft.configuration, self.experiment, draft_table(draft)["columns"])
        self.assertFalse(Procedure.objects.exists())

    def test_procedure_configuration_reuses_against_reordered_upload_and_does_not_contain_animals(self):
        first = self.draft("Date,Mouse ID\n28.04.26,OFFICIAL-1\n")
        first.configuration["procedure_drafts"] = [self.procedure_entry()]
        second = self.draft("Mouse ID,Date\nOFFICIAL-1,30.04.26\n")
        second.configuration = json.loads(json.dumps(first.configuration))
        self.assertEqual(len(build_review(second)["procedures"]), 1)
        self.assertEqual(set(second.configuration["procedure_drafts"][0]), {"procedure_type", "date", "lab_member", "lab_book_page"})

    def test_procedure_failure_rolls_back_recordings_animals_and_approved_updates(self):
        draft = self.draft("Date,Mouse ID,Sex\n28.04.26,OFFICIAL-1,Female\n28.04.26,NEW-ID,Female\n")
        draft.configuration["defaults"].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk)
        draft.configuration["procedure_drafts"] = [self.procedure_entry(), self.procedure_entry()]
        preview = self.reviewed(draft)
        original = Procedure.save
        calls = []
        def fail_second(procedure, *args, **kwargs):
            calls.append(procedure)
            if len(calls) == 2:
                raise ValidationError("Forced procedure failure")
            return original(procedure, *args, **kwargs)
        with patch.object(Procedure, "save", autospec=True, side_effect=fail_second):
            with self.assertRaisesMessage(ValidationError, "Forced procedure failure"):
                commit_import(draft.pk, self.user, self.experiment, [change["token"] for change in preview["changes"]])
        self.assertFalse(Recording.objects.exists())
        self.assertFalse(Procedure.objects.exists())
        self.assertFalse(Mouse.objects.filter(pk="NEW-ID").exists())
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.sex, "")
        draft.refresh_from_db()
        self.assertIsNone(draft.completed_at)

    def test_reused_procedure_editor_still_creates_and_validates_add_animals_procedures(self):
        url = reverse("lab:add_litter_licence_protocol", args=[self.protocol.licence_reference, self.protocol.protocol_number])
        entry = self.procedure_entry()
        data = {"mouse_id_prefix": "PROCEDURE-", "mouse_numbers": "1-2", "protocol": self.protocol.pk, "crossing_definition": self.crossing.pk, "procedure_drafts": json.dumps([entry])}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Procedure.objects.count(), 2)
        self.assertEqual(set(Procedure.objects.values_list("mouse_id", flat=True)), {"PROCEDURE-1", "PROCEDURE-2"})
        self.protocol.allows_regulated_procedures = False
        self.protocol.save()
        response = self.client.post(url, {**data, "mouse_id_prefix": "RESTRICTED-"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("procedure_drafts", response.context["form"].errors)
        self.assertEqual(Procedure.objects.count(), 2)
