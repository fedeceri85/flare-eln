from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import (
    Crossing,
    CrossingLine,
    Experiment,
    Mouse,
    MouseGenotype,
    MouseLine,
    Procedure,
    ProcedureType,
    Project,
    Protocol,
    Recording,
    RecordingField,
    RecordingType,
    SERVICES_PROJECT_NAME,
    Species,
)

admin.site.register(Protocol)


@admin.register(Species)
class SpeciesAdmin(admin.ModelAdmin):
    list_display = ("name", "active")
    list_filter = ("active",)
    search_fields = ("name",)


# @admin.register(MouseLine)
# class MouseLineAdmin(admin.ModelAdmin):
#     list_display = ("name", "species", "is_wild_type", "active")
#     list_filter = ("species", "is_wild_type", "active")
#     search_fields = ("name",)


# class CrossingLineInline(admin.TabularInline):
#     model = CrossingLine
#     extra = 1
#     autocomplete_fields = ("line",)


# @admin.register(Crossing)
# class CrossingAdmin(admin.ModelAdmin):
#     list_display = (
#         "display_name",
#         "species",
#         "inferred_crossing_type",
#         "active",
#     )
#     list_filter = ("species", "active")
#     search_fields = ("display_name", "canonical_key")
#     inlines = [CrossingLineInline]


# @admin.register(MouseGenotype)
# class MouseGenotypeAdmin(admin.ModelAdmin):
#     list_display = ("mouse", "mouse_line", "zygosity")
#     list_filter = ("zygosity", "mouse_line__species", "mouse_line")
#     search_fields = ("mouse__mouse_id", "mouse_line__name")
#     autocomplete_fields = ("mouse", "mouse_line")

@admin.register(ProcedureType)
class ProcedureTypeAdmin(admin.ModelAdmin):
    search_fields = ("name",)
    list_display = ("name", "description")
class ProcedureInline(admin.TabularInline):
    model = Procedure
    extra = 0
    autocomplete_fields = ("procedure_type",)


# @admin.register(Mouse)
# class MouseAdmin(admin.ModelAdmin):
#     list_display = (
#         "mouse_id",
#         "genotype",
#         "breeding_pair",
#         "crossing",
#         "sex",
#         "status",
#         "date_of_birth",
#         "cull_date",
#         "protocol"
#     )

#     search_fields = (
#         "mouse_id",
#         "tattoo",
#         "genotype",
#         "crossing",
#     )

#     list_filter = (
#         "sex",
#         "status",
#     )

#     inlines = [ProcedureInline]

#     ordering = ("mouse_id",)

#     list_per_page = 50

#     def formfield_for_dbfield(self, db_field, request, **kwargs):
#         if db_field.name == "tattoo":
#             kwargs["label"] = "Local identifier"

#         return super().formfield_for_dbfield(db_field, request, **kwargs)


# @admin.register(Project)
# class ProjectAdmin(admin.ModelAdmin):
#     list_display = ("name", "is_service", "active", "start_date", "end_date")
#     list_filter = ("is_service", "active")
#     search_fields = ("name",)

#     def get_readonly_fields(self, request, obj=None):
#         readonly_fields = list(super().get_readonly_fields(request, obj))

#         if is_builtin_services_project(obj):
#             readonly_fields.extend(["name", "active", "is_service"])

#         return tuple(readonly_fields)

#     def has_delete_permission(self, request, obj=None):
#         if is_builtin_services_project(obj):
#             return False

#         return super().has_delete_permission(request, obj)

#     def delete_model(self, request, obj):
#         if is_builtin_services_project(obj):
#             raise PermissionDenied("The built-in Services project cannot be deleted.")

#         return super().delete_model(request, obj)

#     def delete_queryset(self, request, queryset):
#         if queryset.filter(name=SERVICES_PROJECT_NAME).exists():
#             raise PermissionDenied("The built-in Services project cannot be deleted.")

#         return super().delete_queryset(request, queryset)


# def is_builtin_services_project(project):
#     return bool(project and project.name == SERVICES_PROJECT_NAME)


# @admin.register(Experiment)
# class ExperimentAdmin(admin.ModelAdmin):
#     list_display = (
#         "name",
#         "project",
#         "recording_type",
#         "archived",
#         "start_date",
#         "end_date",
#     )
#     list_filter = ("project", "recording_type", "archived")
#     search_fields = ("name",)

# @admin.register(Procedure)
# class ProcedureAdmin(admin.ModelAdmin):
#     autocomplete_fields = ("mouse",)

#     list_display = (
#         "mouse",
#         "procedure_type",
#         "date",
#         "lab_member",
#         "lab_book_page",
#     )
#     list_filter = ("procedure_type",)
#     search_fields = ("mouse__mouse_id", "procedure_type__name")


class RecordingFieldInline(admin.TabularInline):
    model = RecordingField
    extra = 1
    fields = (
        "key",
        "label",
        "data_type",
        "required",
        "choices",
        "units",
        "default_value",
        "active",
        "display_in_table",
        "filterable",
        "order",
    )

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RecordingType)
class RecordingTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "active")
    list_filter = ("active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [RecordingFieldInline]


# @admin.register(Recording)
# class RecordingAdmin(admin.ModelAdmin):
#     list_display = (
#         "recording_type",
#         "owner",
#         "mice_display",
#         "experiment",
#         "recording_date",
#         "sequence_number",
#         "data_path",
#     )
#     list_filter = (
#         "recording_type",
#         "experiment",
#         "owner",
#         "recording_date",
#     )
#     search_fields = (
#         "owner__username",
#         "owner__first_name",
#         "owner__last_name",
#         "mice__mouse_id",
#         "experiment__name",
#         "recording_type__name",
#         "data_path",
#     )
#     readonly_fields = (
#         "owner",
#         "recording_type",
#         "mice_display",
#         "experiment",
#         "recording_date",
#         "sequence_number",
#         "data_path",
#         "notes",
#         "values",
#     )

#     def has_add_permission(self, request):
#         return False

#     def has_change_permission(self, request, obj=None):
#         return False

#     def has_delete_permission(self, request, obj=None):
#         return False

#     def has_view_permission(self, request, obj=None):
#         return True

#     @admin.display(description="Mice")
#     def mice_display(self, obj):
#         return obj.mice_label
