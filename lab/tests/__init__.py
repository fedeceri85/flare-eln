"""Feature tests with compatibility for historical ``lab.tests.ClassName`` labels.

Keep exports lazy: importing test classes here would make unittest discover them
again in addition to their feature modules.
"""

from importlib import import_module


_LEGACY_EXPORTS = {
    "AddLitterTests": "test_litters",
    "AuthenticatedTestCase": "_support",
    "AuthenticationTests": "test_auth_admin",
    "EditSelectedMiceTests": "test_bulk_mice",
    "ExperimentsPageTests": "test_projects_services",
    "ImportMiceCommandTests": "test_import",
    "LinesCrossingsPageTests": "test_genetics",
    "MicePageTests": "test_mice_list",
    "MouseIdentityAndDeletionTests": "test_mouse_identity",
    "ProcedurePageLicenceTests": "test_procedures",
    "ProjectAdminTests": "test_auth_admin",
    "ProjectsPageTests": "test_projects_services",
    "RecordingAdminTests": "test_auth_admin",
    "RecordingDataMigrationTests": "test_migrations",
    "RecordingMiceMigrationTests": "test_migrations",
    "RecordingModelTests": "test_recording_models",
    "RecordingPathsMigrationTests": "test_migrations",
    "RecordingsPageTests": "test_recordings",
    "RenameMultipleCrossingTypeMigrationTests": "test_migrations",
    "ReportsPageTests": "test_reports",
    "ServicesPageTests": "test_projects_services",
    "ServicesSetupMigrationTests": "test_migrations",
    "SingleLineCrossingBackfillMigrationTests": "test_migrations",
    "StructuredCrossingModelTests": "test_genetics",
    "StructuredCrossingsMigrationTests": "test_migrations",
    "create_recording": "_support",
    "create_test_crossing": "_support",
    "crossing_post_data": "_support",
}


def __getattr__(name):
    module_name = _LEGACY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.{module_name}"), name)
