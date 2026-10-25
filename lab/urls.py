from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.urls import path, register_converter

from . import converters, views


app_name = "lab"

register_converter(converters.MousePkConverter, "mousepk")


def protected(view):
    return login_required(view, login_url="lab:login")


urlpatterns = [
    path("import-images/<uuid:image_id>/", protected(views.import_image_file), name="import_image_file"),
    path("projects/<int:project_pk>/experiments/<int:experiment_pk>/recordings/import/<uuid:draft_id>/images/", protected(views.import_shared_images), name="import_shared_images"),
    path("settings/image-storage/", protected(views.image_storage_settings), name="image_storage_settings"),
    path("recording-images/<uuid:image_id>/", protected(views.recording_image_file), name="recording_image_file"),
    path("projects/<int:project_pk>/experiments/<int:experiment_pk>/recordings/<int:recording_pk>/images/", protected(views.recording_images), name="recording_images"),
    path("settings/", protected(views.settings_page), name="settings_page"),
    path("settings/genotype-categories/", protected(views.genotype_categories), name="genotype_categories"),
    path("settings/genotype-categories/new/", protected(views.edit_genotype_category), name="add_genotype_category"),
    path("settings/genotype-categories/<int:pk>/edit/", protected(views.edit_genotype_category), name="edit_genotype_category"),
    path("settings/recording-types/", protected(views.recording_types), name="recording_types"),
    path("settings/recording-types/new/", protected(views.edit_recording_type), name="add_recording_type"),
    path("settings/recording-types/<int:pk>/edit/", protected(views.edit_recording_type), name="edit_recording_type"),
    path("settings/recording-types/<int:pk>/delete/", protected(views.delete_recording_type), name="delete_recording_type"),
    path("settings/recording-types/<int:pk>/fields/new/", protected(views.edit_recording_field), name="add_recording_field"),
    path("settings/recording-types/<int:pk>/fields/<int:field_pk>/edit/", protected(views.edit_recording_field), name="edit_recording_field"),
    path("settings/import-templates/", protected(views.import_templates), name="import_templates"),
    path("settings/import-templates/<int:pk>/rename/", protected(views.rename_import_template), name="rename_import_template"),
    path("settings/import-templates/<int:pk>/delete/", protected(views.delete_import_template), name="delete_import_template"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="lab/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path(
        "logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),
    path(
        "",
        protected(views.home),
        name="home",
    ),
    path(
        "reports/",
        protected(views.reports_page),
        name="reports_page",
    ),
    path(
        "procedures/",
        protected(views.procedure_page),
        name="procedure_page",
    ),
    path(
        (
            "procedures/licence/<path:licence_reference>/"
            "protocol/<int:protocol_number>/add-mouse/"
        ),
        protected(views.add_mouse),
        name="add_mouse_licence_protocol",
    ),
    path(
        (
            "procedures/licence/<path:licence_reference>/"
            "protocol/<int:protocol_number>/add-litter/"
        ),
        protected(views.add_litter),
        name="add_litter_licence_protocol",
    ),
    path(
        (
            "procedures/licence/<path:licence_reference>/"
            "protocol/<int:protocol_number>/edit-selected/"
        ),
        protected(views.edit_selected_mice),
        name="edit_selected_mice_licence_protocol",
    ),
    path(
        "procedures/licence/<path:licence_reference>/protocol/<int:protocol_number>/view-selected/",
        protected(views.view_selected_mice),
        name="view_selected_mice_licence_protocol",
    ),
    path(
        (
            "procedures/licence/<path:licence_reference>/"
            "protocol/<int:protocol_number>/delete-selected/"
        ),
        protected(views.delete_selected_mice),
        name="delete_selected_mice_licence_protocol",
    ),
    path(
        (
            "procedures/licence/<path:licence_reference>/"
            "protocol/<int:protocol_number>/"
        ),
        protected(views.procedure_page),
        name="procedure_page_licence_protocol",
    ),
    path(
        "procedures/licence/<path:licence_reference>/",
        protected(views.procedure_page),
        name="procedure_page_licence",
    ),
    path(
        "procedures/protocol/<int:protocol_number>/",
        protected(views.procedure_page),
        name="procedure_page_protocol",
    ),
    path(
        "procedures/protocol/<int:protocol_number>/add-mouse/",
        protected(views.add_mouse),
        name="add_mouse",
    ),
    path(
        "procedures/protocol/<int:protocol_number>/add-litter/",
        protected(views.add_litter),
        name="add_litter",
    ),
    path(
        "procedures/protocol/<int:protocol_number>/edit-selected/",
        protected(views.edit_selected_mice),
        name="edit_selected_mice",
    ),
    path(
        "procedures/protocol/<int:protocol_number>/delete-selected/",
        protected(views.delete_selected_mice),
        name="delete_selected_mice",
    ),
    path(
        "procedures/mouse-id-preview/",
        protected(views.mouse_id_preview),
        name="mouse_id_preview",
    ),
    path(
        "lines-crossings/",
        protected(views.lines_crossings_page),
        name="lines_crossings_page",
    ),
    path(
        "lines-crossings/line-lookup/",
        protected(views.line_lookup),
        name="line_lookup",
    ),
    path(
        "lines-crossings/crossing-lookup/",
        protected(views.crossing_lookup),
        name="crossing_lookup",
    ),
    path(
        "lines-crossings/crossing-genotype-fields/",
        protected(views.crossing_genotype_fields),
        name="crossing_genotype_fields",
    ),
    path(
        "projects/",
        protected(views.projects_page),
        name="projects_page",
    ),
    path(
        "projects/add/",
        protected(views.add_project),
        name="add_project",
    ),
    path(
        "projects/<int:project_pk>/edit/",
        protected(views.edit_project),
        name="edit_project",
    ),
    path(
        "projects/<int:project_pk>/experiments/",
        protected(views.experiments_page),
        name="experiments_page",
    ),
    path(
        "projects/<int:project_pk>/experiments/add/",
        protected(views.add_experiment),
        name="add_experiment",
    ),
    path(
        "projects/<int:project_pk>/experiments/<int:experiment_pk>/edit/",
        protected(views.edit_experiment),
        name="edit_experiment",
    ),
    path(
        "projects/<int:project_pk>/experiments/<int:experiment_pk>/recordings/",
        protected(views.recordings_page),
        name="recordings_page",
    ),
    path(
        "services/",
        protected(views.services_page),
        name="services_page",
    ),
    path(
        "projects/<int:project_pk>/experiments/<int:experiment_pk>/recordings/import/",
        protected(views.import_recordings),
        name="import_recordings",
    ),
    path(
        "projects/<int:project_pk>/experiments/<int:experiment_pk>/recordings/import/<uuid:draft_id>/",
        protected(views.import_recordings),
        name="import_recordings_draft",
    ),
    path(
        (
            "projects/<int:project_pk>/experiments/<int:experiment_pk>/"
            "recordings/add/"
        ),
        protected(views.add_recording),
        name="add_recording",
    ),
    path(
        (
            "projects/<int:project_pk>/experiments/<int:experiment_pk>/"
            "recordings/select-mouse/"
        ),
        protected(views.select_recording_mouse),
        name="select_recording_mouse",
    ),
    path(
        (
            "projects/<int:project_pk>/experiments/<int:experiment_pk>/"
            "recordings/duplicate/"
        ),
        protected(views.duplicate_recording),
        name="duplicate_recording",
    ),
    path(
        "projects/<int:project_pk>/experiments/<int:experiment_pk>/recordings/delete-selected/",
        protected(views.delete_selected_recordings),
        name="delete_selected_recordings",
    ),
    path(
        (
            "projects/<int:project_pk>/experiments/<int:experiment_pk>/"
            "recordings/<int:recording_pk>/edit/"
        ),
        protected(views.edit_recording),
        name="edit_recording",
    ),
    path(
        "projects/<int:project_pk>/experiments/<int:experiment_pk>/recordings/<int:recording_pk>/animals/",
        protected(views.recording_animals),
        name="recording_animals",
    ),
    path(
        (
            "projects/<int:project_pk>/experiments/<int:experiment_pk>/"
            "recordings/<int:recording_pk>/delete/"
        ),
        protected(views.delete_recording),
        name="delete_recording",
    ),
    path(
        "mice/",
        protected(views.mice_page),
        name="mice_page",
    ),
    path(
        "mice/add-mouse/",
        protected(views.add_mouse),
        name="add_mouse_all",
    ),
    path(
        "mice/add-litter/",
        protected(views.add_litter),
        name="add_litter_all",
    ),
    path(
        "mice/edit-selected/",
        protected(views.edit_selected_mice_all),
        name="edit_selected_mice_all",
    ),
    path(
        "mice/delete-selected/",
        protected(views.delete_selected_mice_all),
        name="delete_selected_mice_all",
    ),
    path(
        "mice/lookup/",
        protected(views.mouse_lookup),
        name="mouse_lookup",
    ),
    path(
        "mice/<mousepk:mouse_pk>/",
        protected(views.animal_summary),
        name="animal_summary",
    ),
    path(
        "mice/<mousepk:mouse_pk>/edit/",
        protected(views.edit_mouse),
        name="edit_mouse",
    ),
    path(
        "mice/<mousepk:mouse_pk>/rename/",
        protected(views.rename_mouse),
        name="rename_mouse",
    ),
    path(
        "mice/<mousepk:mouse_pk>/delete/",
        protected(views.delete_mouse),
        name="delete_mouse",
    ),
    path(
        "mice/<mousepk:mouse_pk>/procedures/<int:procedure_pk>/edit/",
        protected(views.edit_mouse_procedure),
        name="edit_mouse_procedure",
    ),
    path(
        "mice/<mousepk:mouse_pk>/procedures/<int:procedure_pk>/delete/",
        protected(views.delete_mouse_procedure),
        name="delete_mouse_procedure",
    ),
]
