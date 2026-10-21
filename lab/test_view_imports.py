"""Keep endpoint and helper imports stable while implementations are modular."""

from django.test import SimpleTestCase
from django.urls import resolve, reverse

from . import views
from .views import common, genetics, mice, procedures, projects, recordings, reports


class ViewImportCompatibilityTests(SimpleTestCase):
    def test_public_endpoints_resolve_to_the_feature_implementation(self):
        routes = [
            ("home", [], reports.home),
            ("reports_page", [], reports.reports_page),
            ("procedure_page", [], procedures.procedure_page),
            ("mice_page", [], mice.mice_page),
            ("add_litter_all", [], mice.add_litter),
            ("edit_selected_mice_all", [], mice.edit_selected_mice_all),
            ("delete_selected_mice_all", [], mice.delete_selected_mice_all),
            ("edit_mouse_procedure", ["Mouse / 1", 7], procedures.edit_mouse_procedure),
            ("projects_page", [], projects.projects_page),
            ("recordings_page", [1, 2], recordings.recordings_page),
            ("crossing_genotype_fields", [], genetics.crossing_genotype_fields),
        ]
        for name, args, implementation in routes:
            with self.subTest(route=name):
                callback = resolve(reverse(f"lab:{name}", args=args)).func
                self.assertIs(callback.__wrapped__, implementation)
                self.assertIs(getattr(views, implementation.__name__), implementation)

    def test_existing_helper_imports_remain_available(self):
        helpers = [
            common.safe_return_url,
            common.selected_mice_for_protocol,
            common.selected_mice_for_ids,
            mice.apply_bulk_mouse_updates,
            mice.create_litter_mice,
            mice.rename_mouse_identity,
            mice.possible_duplicate_mouse_warning,
            reports.build_home_office_return_report,
        ]
        for helper in helpers:
            with self.subTest(helper=helper.__name__):
                self.assertIs(getattr(views, helper.__name__), helper)
