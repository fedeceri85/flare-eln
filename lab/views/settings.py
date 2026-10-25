"""Shared application settings and spreadsheet template metadata management."""
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from ..forms.import_templates import ImportTemplateNameForm
from ..forms.settings import GenotypeCategoryForm, RecordingFieldForm, RecordingTypeForm
from ..models import GenotypeCategory, RecordingField, RecordingType, SpreadsheetImportTemplate


def administrator_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def settings_page(request):
    cards = [{
        "title": "Import templates",
        "description": "Rename or delete spreadsheet column templates for each experiment type.",
        "url": reverse("lab:import_templates"),
    }]
    if request.user.is_staff:
        cards.extend([
          #  {"title": "Image storage", "description": "Choose the folder for new recording images.", "url": reverse("lab:image_storage_settings")},
            {"title": "Genotype categories", "description": "Manage genotype labels, ordering and availability.", "url": reverse("lab:genotype_categories")},
            {"title": "Recording types", "description": "Manage experiment types and their recording fields.", "url": reverse("lab:recording_types")},
        ])
    return render(request, "lab/settings.html", {"cards": cards})


def save_settings_form(request, form, success_url):
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error(None, "These details conflict with an existing entry. Review the name or key and try again.")
        else:
            messages.success(request, "Changes saved.")
            return redirect(success_url() if callable(success_url) else success_url)
    return None


@administrator_required
def genotype_categories(request):
    return render(request, "lab/genotype_categories.html", {"categories": GenotypeCategory.objects.all()})


@administrator_required
def edit_genotype_category(request, pk=None):
    category = get_object_or_404(GenotypeCategory, pk=pk) if pk is not None else None
    form = GenotypeCategoryForm(request.POST if request.method == "POST" else None, instance=category)
    response = save_settings_form(request, form, reverse("lab:genotype_categories"))
    return response or render(request, "lab/settings_form.html", {
        "form": form, "title": "Edit genotype category" if category else "Add genotype category",
        "parent_url": reverse("lab:genotype_categories"),
        "help_note": "Retire a category by unchecking Active. Historical genotype results are kept.",
    })


@administrator_required
def recording_types(request):
    return render(request, "lab/recording_types.html", {"recording_types": RecordingType.objects.all()})


@administrator_required
def edit_recording_type(request, pk=None):
    recording_type = get_object_or_404(RecordingType, pk=pk) if pk is not None else None
    form = RecordingTypeForm(request.POST if request.method == "POST" else None, instance=recording_type)
    response = save_settings_form(request, form, lambda: reverse("lab:edit_recording_type", args=[form.instance.pk]))
    if response:
        return response
    return render(request, "lab/settings_form.html", {
        "form": form, "title": "Edit recording type" if recording_type else "Add recording type",
        "recording_type": recording_type, "parent_url": reverse("lab:recording_types"),
        "recording_fields": recording_type.fields.all() if recording_type else None,
        "show_recording_fields": recording_type is not None,
    })


@administrator_required
def edit_recording_field(request, pk, field_pk=None):
    recording_type = get_object_or_404(RecordingType, pk=pk)
    field = get_object_or_404(RecordingField, pk=field_pk, recording_type=recording_type) if field_pk is not None else None
    form = RecordingFieldForm(request.POST if request.method == "POST" else None, instance=field, recording_type=recording_type)
    parent_url = reverse("lab:edit_recording_type", args=[recording_type.pk])
    response = save_settings_form(request, form, parent_url)
    return response or render(request, "lab/settings_form.html", {
        "form": form, "title": "Edit recording field" if field else "Add recording field",
        "recording_type": recording_type, "parent_url": parent_url,
        "help_note": "Retire fields by unchecking Active. Fields with historical values keep their stored key.",
    })


@administrator_required
def delete_recording_type(request, pk):
    recording_type = get_object_or_404(RecordingType, pk=pk)
    error = None
    if request.method == "POST":
        try:
            recording_type.delete()
        except ProtectedError:
            error = "This recording type is linked to experiments or recordings. Deactivate it instead of deleting it."
        else:
            messages.success(request, "Recording type deleted.")
            return redirect("lab:recording_types")
    return render(request, "lab/recording_type_delete.html", {"recording_type": recording_type, "error": error})


def import_templates(request):
    templates = SpreadsheetImportTemplate.objects.select_related("recording_type", "created_by")
    return render(request, "lab/import_templates.html", {"templates": templates})


def rename_import_template(request, pk):
    template = get_object_or_404(SpreadsheetImportTemplate, pk=pk)
    form = ImportTemplateNameForm(request.POST if request.method == "POST" else None, instance=template)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error("name", "A template with this name already exists for this experiment type.")
        else:
            messages.success(request, "Template renamed.")
            return redirect("lab:import_templates")
    return render(request, "lab/import_template_edit.html", {"template": template, "form": form})


def delete_import_template(request, pk):
    template = get_object_or_404(SpreadsheetImportTemplate, pk=pk)
    if request.method == "POST":
        template.delete()
        messages.success(request, "Template deleted. Mappings already applied to imports are unchanged.")
        return redirect("lab:import_templates")
    return render(request, "lab/import_template_delete.html", {"template": template})
