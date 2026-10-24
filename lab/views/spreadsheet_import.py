"""Thin wizard controller; mapping and import behavior lives in services."""
from collections import defaultdict
from pathlib import Path

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from ..models import Protocol, SpreadsheetImportDraft, SpreadsheetImportTemplate
from ..forms.import_templates import ImportTemplateNameForm
from ..forms.procedures import BulkProcedureCreateForm
from ..spreadsheet_import.engine import build_review, commit_import, digest, draft_table, identity_signature, selected_rows
from ..spreadsheet_import.forms import AnimalsForm, GeneticsForm, MappingForm, ProceduresForm, SetupForm, SourceForm, UploadForm
from ..spreadsheet_import.mapping import new_definition, validate_definition
from ..spreadsheet_import.source import read_upload, table_from_sheet
from ..spreadsheet_import.templates import capture_mapping, match_mapping, mapped_form_data
from .common import experiment_for_project_or_404

STEPS = [("source", "Source & rows"), ("setup", "Import settings"), ("mapping", "Recording fields"), ("animals", "Animals"), ("genetics", "Crossings & genotypes"), ("procedures", "Add procedures"), ("review", "Review & import")]


def import_recordings(request, project_pk, experiment_pk, draft_id=None):
    project, experiment = experiment_for_project_or_404(project_pk, experiment_pk)
    recordings_url = reverse("lab:recordings_page", args=[project.pk, experiment.pk])
    if experiment.recording_type_id is None:
        messages.error(request, "Select an experiment type before importing recordings.")
        return redirect(recordings_url)
    base_url = reverse("lab:import_recordings", args=[project.pk, experiment.pk])
    context = {"project": project, "experiment": experiment, "recordings_url": recordings_url, "base_url": base_url}
    if draft_id is None:
        form = UploadForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            upload = form.cleaned_data["spreadsheet"]
            try:
                source = read_upload(upload)
                if not source or not any(s["rows"] for s in source):
                    raise ValidationError("The spreadsheet contains no rows.")
                sheet = next(s for s in source if s["rows"])
                table = table_from_sheet(sheet, 1)
                draft = SpreadsheetImportDraft.objects.create(
                    owner=request.user, experiment=experiment, filename=Path(upload.name).name[:255], source=source,
                    configuration=new_definition(experiment, table["columns"]),
                    state={"sheet": sheet["name"], "header_row": 1, "selected_rows": [r["number"] for r in table["rows"] if not r["flag"]]},
                )
                return redirect(reverse("lab:import_recordings_draft", args=[project.pk, experiment.pk, draft.pk]))
            except ValidationError as exc:
                form.add_error("spreadsheet", exc)
        return render(request, "lab/import_recordings.html", {**context, "upload_form": form})

    # Serialize wizard edits too, so another tab cannot silently overwrite a review.
    with transaction.atomic():
        draft = get_object_or_404(SpreadsheetImportDraft.objects.select_for_update(), pk=draft_id, owner=request.user, experiment=experiment)
        if draft.expires_at <= timezone.now():
            return render(request, "lab/import_recordings.html", {**context, "expired": True}, status=410)
        if draft.completed_at:
            return render(request, "lab/import_recordings.html", {**context, "result": draft.state.get("result", {})})
        step = request.POST.get("step") if request.method == "POST" else request.GET.get("step", "source")
        if step not in dict(STEPS):
            raise Http404("Unknown import step.")
        table = draft_table(draft)
        form_class = {"source": SourceForm, "setup": SetupForm, "mapping": MappingForm, "animals": AnimalsForm, "procedures": ProceduresForm, "genetics": GeneticsForm}.get(step)
        errors, preview = [], None
        revision = digest([draft.configuration, draft.state])
        data = request.POST if request.method == "POST" else None
        template_action = request.POST.get("template_action") if step == "mapping" else None
        template_name_form = ImportTemplateNameForm(
            data=data if template_action == "save" else None,
            recording_type=experiment.recording_type, prefix="template",
        )
        try:
            if step != "source":
                selected_rows(draft, table)
            form = form_class(draft, data=data) if step == "source" else form_class(draft, table, data=data) if form_class else None
            if template_action == "save":
                form.require_identity = False
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect(request.path + "?step=source")
        if request.method == "POST":
            if request.POST.get("revision") != revision:
                errors.append("This import changed in another tab. Reload this page before continuing.")
            elif template_action in {"save", "apply"}:
                try:
                    if template_action == "save":
                        payload = capture_mapping(experiment, table["columns"], request.POST)
                        if template_name_form.is_valid():
                            template = template_name_form.save(commit=False)
                            template.mapping = payload
                            template.created_by = request.user
                            try:
                                with transaction.atomic():
                                    template.save()
                            except IntegrityError:
                                template_name_form.add_error("name", "A template with this name already exists for this experiment type.")
                            else:
                                messages.success(request, f'Saved template “{template.name}”.')
                                template_name_form = ImportTemplateNameForm(recording_type=experiment.recording_type, prefix="template")
                    else:
                        template = SpreadsheetImportTemplate.objects.filter(
                            pk=request.POST.get("import_template") or None,
                            recording_type=experiment.recording_type,
                        ).first()
                        if template is None:
                            raise ValidationError("Choose an available template for this experiment type.")
                        resolved, warnings = match_mapping(template, experiment, table["columns"])
                        form = MappingForm(draft, table, data=mapped_form_data(
                            request.POST, experiment, table["columns"], resolved,
                        ), require_identity=False)
                        if form.is_valid():
                            old_identity = identity_signature(draft.configuration)
                            draft.configuration = form.definition()
                            if identity_signature(draft.configuration) != old_identity:
                                draft.state.pop("animals", None)
                            draft.state.pop("review_fingerprint", None)
                            draft.save(update_fields=["configuration", "state"])
                            messages.success(request, f'Applied template “{template.name}”. Review the column associations before continuing.')
                            for warning in warnings:
                                messages.warning(request, warning)
                            return redirect(request.path + "?step=mapping")
                except (ValidationError, ValueError, TypeError) as exc:
                    errors.extend(exc.messages if isinstance(exc, ValidationError) else ["Choose an available template for this experiment type."])
            elif step == "review":
                if request.POST.get("review_fingerprint") != draft.state.get("review_fingerprint"):
                    errors.append("This review is out of date. Refresh the review before importing.")
                else:
                    try:
                        result = commit_import(draft.pk, request.user, experiment, request.POST.getlist("approve"))
                        summary = f"Imported {result['recordings']} recordings; created {result['created_animals']} animals and updated {result['updated_animals']} animals."
                        if result.get("procedures"):
                            summary += f" Added {result['procedures']} procedures."
                        messages.success(request, summary)
                        return redirect(recordings_url)
                    except (ValidationError, IntegrityError) as exc:
                        errors.extend(exc.messages if isinstance(exc, ValidationError) else ["An animal changed or an ID is now in use. Refresh the review and resolve it before importing."])
            elif form.is_valid():
                try:
                    if step == "source":
                        sheet = next(s for s in draft.source if s["name"] == form.cleaned_data["sheet"])
                        new_table = table_from_sheet(sheet, form.cleaned_data["header_row"])
                        changed = (sheet["name"], form.cleaned_data["header_row"]) != (draft.state["sheet"], draft.state["header_row"])
                        if changed:
                            draft.state = {"sheet": sheet["name"], "header_row": form.cleaned_data["header_row"], "selected_rows": [r["number"] for r in new_table["rows"] if not r["flag"]]}
                            draft.configuration = new_definition(experiment, new_table["columns"])
                        else:
                            allowed = {str(r["number"]) for r in table["rows"]}
                            numbers = request.POST.getlist("selected_rows")
                            if any(n not in allowed for n in numbers):
                                raise ValidationError("Select rows shown in the source preview.")
                            draft.state["selected_rows"] = sorted({int(n) for n in numbers})
                            if not numbers:
                                raise ValidationError("Select at least one data row.")
                        if changed:
                            request_next = "source"
                        else:
                            request_next = request.POST.get("next_step", "setup")
                    elif step in {"setup", "mapping"}:
                        old_identity = identity_signature(draft.configuration)
                        draft.configuration = form.definition()
                        validate_definition(draft.configuration, experiment, table["columns"])
                        # Changed identity rules invalidate file-specific animal resolutions.
                        if identity_signature(draft.configuration) != old_identity:
                            draft.state.pop("animals", None)
                        request_next = request.POST.get("next_step", "mapping" if step == "setup" else "animals")
                    else:
                        form.apply(draft)
                        request_next = request.POST.get("next_step", {"animals": "genetics", "genetics": "procedures", "procedures": "review"}[step])
                    draft.state.pop("review_fingerprint", None)
                    draft.save(update_fields=["configuration", "state"])
                    return redirect(request.path + "?step=" + (request_next if request_next in dict(STEPS) else step))
                except ValidationError as exc:
                    form.add_error(None, exc)
        if step == "review":
            try:
                preview = build_review(draft)
                if request.method == "GET":
                    draft.state["review_fingerprint"] = preview["fingerprint"]
                    draft.save(update_fields=["state"])
                    revision = digest([draft.configuration, draft.state])
            except ValidationError as exc:
                errors.extend(exc.messages)
        rows = table["rows"]
        selected = set(draft.state.get("selected_rows", []))
        if step == "source" and request.method == "POST":
            selected = {int(n) for n in request.POST.getlist("selected_rows") if n.isdigit()}
        procedures_by_animal = defaultdict(list)
        if preview:
            for procedure in preview["procedures"]:
                procedures_by_animal[procedure["animal_key"]].append(procedure)
        context.update(
            import_templates=SpreadsheetImportTemplate.objects.filter(recording_type=experiment.recording_type),
            template_name_form=template_name_form,
            template_dialog_open=template_action == "save" and (bool(errors) or bool(template_name_form.errors)),
            template_save_errors=errors if template_action == "save" else [],
            draft=draft, step=step, form=form, table=table, errors=errors, preview=preview, revision=revision,
            review_animals=[{**animal, "procedures": procedures_by_animal[animal["key"]]} for animal in preview["animals"]] if preview else [],
            source_rows=[{**r, "selected": r["number"] in selected} for r in rows],
            procedure_editor_scope="import", litter_procedure_form=BulkProcedureCreateForm() if step == "procedures" else None,
            procedure_fields_enabled=step == "procedures" and Protocol.objects.filter(
                pk=draft.configuration.get("defaults", {}).get("protocol_id") or None,
                allows_regulated_procedures=True,
            ).exists(),
            tabs=[{"key": key, "label": label, "active": key == step, "url": request.path + "?step=" + key} for key, label in STEPS],
        )
        return render(request, "lab/import_recordings.html", context)
