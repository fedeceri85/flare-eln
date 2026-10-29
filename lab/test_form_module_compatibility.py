"""The form package preserves imports used by views, services and older callers."""

from importlib import import_module

from django.test import SimpleTestCase

from . import forms


class FormModuleCompatibilityTests(SimpleTestCase):
    def test_existing_public_form_imports_resolve_to_feature_classes(self):
        public_forms = {
            "genetics": ["BulkMouseLineCreateForm", "CrossingCreateForm", "MouseStructuredGeneticsMixin"],
            "mice": ["MouseForm", "MouseEditForm", "MouseRenameForm", "MouseDeleteConfirmForm", "BulkMouseDeleteConfirmForm"],
            "litter": ["LitterCreateForm"],
            "bulk_mice": ["BulkMouseUpdateForm"],
            "procedures": ["BaseProcedureForm", "ProcedureCreateForm", "ProcedureEditForm", "BulkProcedureCreateForm", "ExistingProcedureBulkForm"],
            "projects": ["ProjectForm", "ExperimentForm"],
            "recordings": ["RecordingForm"],
            "reporting": ["HomeOfficeReturnForm"],
        }
        for feature, names in public_forms.items():
            module = import_module(f"lab.forms.{feature}")
            for name in names:
                with self.subTest(feature=feature, name=name):
                    self.assertIs(getattr(forms, name), getattr(module, name))
                    self.assertIn(name, forms.__all__)

    def test_shared_helpers_and_procedure_validation_have_direct_dependencies(self):
        from .forms import genetics, litter, procedures, recordings

        self.assertIs(forms.genotype_field_name, genetics.genotype_field_name)
        self.assertIs(forms.sync_mouse_structured_genetics, genetics.sync_mouse_structured_genetics)
        self.assertIs(forms.individual_mouse_field_prefix, genetics.individual_mouse_field_prefix)
        self.assertIs(forms.parse_recording_paths, recordings.parse_recording_paths)
        self.assertIs(litter.BulkProcedureCreateForm, procedures.BulkProcedureCreateForm)
        self.assertTrue(issubclass(forms.LitterCreateForm, forms.MouseStructuredGeneticsMixin))
        self.assertTrue(issubclass(forms.MouseForm, forms.MouseStructuredGeneticsMixin))
        self.assertTrue(issubclass(forms.MouseEditForm, forms.MouseStructuredGeneticsMixin))

    def test_declared_fields_keep_their_existing_order(self):
        expected = {
            "HomeOfficeReturnForm": ["licence_reference", "start_date", "end_date", "output_mode"],
            "ProjectForm": ["name", "lab_member", "description", "start_date", "end_date", "active"],
            "ExperimentForm": ["name", "description", "start_date", "end_date", "archived", "recording_type_query"],
            "RecordingForm": ["mice", "recording_date", "data_paths", "notes"],
            "MouseEditForm": ["date_of_birth", "marking_type", "tattoo", "breeding_pair", "crossing_definition", "sex", "colour", "status", "protocol_start_date", "cull_date", "protocol", "severity", "notes"],
            "LitterCreateForm": ["marking_type", "no_id_available", "mouse_id_prefix", "mouse_numbers", "breeding_pair", "colour", "date_of_birth", "crossing_definition", "status", "protocol", "protocol_start_date", "cull_date", "severity", "notes", "procedure_drafts"],
            "BaseProcedureForm": ["procedure_type", "date", "lab_member", "lab_book_page"],
        }
        for name, names in expected.items():
            with self.subTest(form=name):
                self.assertEqual(list(getattr(forms, name).base_fields), names)
