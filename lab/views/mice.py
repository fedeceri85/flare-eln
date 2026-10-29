'Mice views and helpers.'

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from urllib.parse import urlencode, urlsplit, urlunsplit
import json
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.contrib import messages
from django.core import signing
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import Model
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from ..parentage import apply_parentage, parentage_transaction, parentage_lock
from ..family import parent_group_version
from ..animal_summary import build_animal_recording_sections
from ..forms import (
    BulkMouseDeleteConfirmForm,
    BulkMouseUpdateForm,
    BulkProcedureCreateForm,
    LitterCreateForm,
    MouseDeleteConfirmForm,
    genotype_field_name,
    sync_mouse_structured_genetics,
)
from ..forms.recordings import RecordingDestinationForm
from ..models import (
    Crossing,
    Mouse,
    Procedure,
    Protocol,
    Recording,
)
from ..procedure_bulk import (
    editor_context,
    handle_procedure_action,
)
from ..ui import (
    apply_filter_specs,
    build_filter_form,
    build_mice_page_filter_specs,
    build_mice_table,
    build_pagination,
    build_record_count,
    build_table_page_size_control,
    mice_page_sortable_fields,
    read_filter_values,
    read_table_page_size,
)
from ..utils import generate_mouse_id, mouse_id_base
from .common import (
    add_recording_url,
    animal_summary_context,
    append_text,
    delete_selected_mice_all_url,
    delete_selected_mice_url,
    edit_selected_mice_all_url,
    edit_selected_mice_url,
    order_mice_page_queryset,
    procedure_protocol_url,
    protocol_by_number_or_404,
    recording_count_for_mice,
    recording_mice_query_value,
    safe_return_url,
    selected_mice_for_ids,
    selected_mice_for_protocol,
    selected_mouse_ids,
    selected_recording_mouse_ids_from_url,
    service_recording_actions_for_mice,
    url_with_query_params,
)


@parentage_transaction
def apply_bulk_mouse_updates(mice, form):
    drafts = form.cleaned_data.get("procedure_drafts", [])
    if drafts:
        locked = list(Mouse.objects.select_for_update().filter(pk__in=[mouse.pk for mouse in mice]).order_by("pk"))
        if len(locked) != len(mice):
            raise ValidationError("The animal selection changed. Reload the editor before saving procedures.")
    if form.cleaned_data.get("parentage_updates"):
        current = list(Mouse.objects.select_for_update().filter(pk__in=[mouse.pk for mouse in mice]).order_by("pk"))
        if parent_group_version(current) != form._parent_group_version:
            raise ValidationError("Parent groups changed. Reload the editor before changing parents.")
    renames = form.cleaned_data.get("mouse_id_renames", {})
    if renames:
        locked = list(Mouse.objects.select_for_update().filter(pk__in=[mouse.pk for mouse in mice]).order_by("pk"))
        if len(locked) != len(mice) or any(
            current.protocol_id != next(mouse.protocol_id for mouse in mice if mouse.pk == current.pk)
            for current in locked
        ):
            raise IntegrityError("The selected mice changed before saving.")
        Mouse.objects.filter(pk__in=renames).update(id_status=Mouse.ID_ASSIGNED)
    mouse_updates = form.selected_mouse_updates()
    structured_genetics_update = form.selected_structured_genetics_update()
    notes_action = form.cleaned_data.get("notes_action")
    notes = form.cleaned_data.get("notes", "").strip()

    changed_identifiers = [
        mouse.pk for mouse in mice
        if "tattoo" in form.cleaned_data.get("individual_updates", {}).get(mouse.pk, {}).get("fields", {})
    ]
    if changed_identifiers:
        # Release old values inside this transaction so identifiers can be swapped.
        list(Mouse.objects.select_for_update().filter(pk__in=changed_identifiers).order_by("pk"))
        Mouse.objects.filter(pk__in=changed_identifiers).update(tattoo="")

    for mouse in mice:
        individual = form.cleaned_data.get("individual_updates", {}).get(mouse.pk, {})
        changed_mouse_fields = []
        combined_updates = {**mouse_updates, **individual.get("fields", {})}

        for field_name, value in combined_updates.items():
            setattr(mouse, field_name, value)
            changed_mouse_fields.append(field_name)

        if notes_action in {"append", "replace"} and "notes" not in individual.get("fields", {}):
            mouse.notes = (
                append_text(mouse.notes, notes)
                if notes_action == "append"
                else notes
            )
            changed_mouse_fields.append("notes")

        if changed_mouse_fields:
            mouse.save(update_fields=changed_mouse_fields)

        individual_genotypes = individual.get("genotype_values", {})
        individual_uncertainty = individual.get("uncertainty_values", {})
        if structured_genetics_update is not None or individual_genotypes or individual_uncertainty or individual.get("crossing_override"):
            if structured_genetics_update is not None:
                crossing, genotype_values, preserve_missing = structured_genetics_update
                uncertainty_values = form.cleaned_data.get("uncertainty_values", {})
            else:
                crossing = individual["crossing"]
                genotype_values, uncertainty_values, preserve_missing = {}, {}, True
            if individual.get("crossing_override"):
                crossing = individual["crossing"]
                preserve_missing = True
            sync_mouse_structured_genetics(
                mouse, crossing, {**genotype_values, **individual_genotypes},
                preserve_missing=preserve_missing,
                uncertainty_values={**uncertainty_values, **individual_uncertainty},
            )

    form.parentage_warnings = list(dict.fromkeys([*getattr(form, "parentage_resolution_warnings", []), *apply_parentage(form.cleaned_data.get("parentage_updates", {}))]))

    # Release all old names first, allowing swaps and chains without collisions.
    temporary_ids = {}
    for old_id in renames:
        temporary_id = "rename-" + uuid4().hex
        while temporary_id in renames.values() or Mouse.objects.filter(pk=temporary_id).exists():
            temporary_id = "rename-" + uuid4().hex
        rename_mouse_identity(old_id, temporary_id)
        temporary_ids[old_id] = temporary_id
    for old_id, new_id in renames.items():
        rename_mouse_identity(temporary_ids[old_id], new_id)

    if drafts:
        final_mice = list(Mouse.objects.select_related("protocol").filter(pk__in=[renames.get(mouse.pk, mouse.pk) for mouse in mice]))
        if len(final_mice) != len(mice) or any(not mouse.protocol.allows_regulated_procedures for mouse in final_mice):
            raise ValidationError("One or more selected animals no longer allow regulated procedures. No changes were saved.")
        for mouse in final_mice:
            for values in drafts:
                Procedure.objects.create(mouse=mouse, **values)


def create_mouse_procedure(mouse, form):
    procedure = form.save(commit=False)
    procedure.mouse = mouse
    procedure.save()
    return procedure


@parentage_transaction
def rename_mouse_identity(mouse_pk, new_mouse_id):
    mouse = Mouse.objects.select_for_update().get(pk=mouse_pk)
    renamed_mouse = Mouse(mouse_id=new_mouse_id)

    for field in Mouse._meta.concrete_fields:
        if field.primary_key:
            continue

        setattr(
            renamed_mouse,
            field.attname,
            deepcopy(getattr(mouse, field.attname)),
        )

    renamed_mouse.id_status = Mouse.ID_ASSIGNED
    # Release the old record's identifier inside this transaction before cloning.
    # The uniqueness rule excludes blank identifiers; rollback restores it on failure.
    if mouse.tattoo:
        Mouse.objects.filter(pk=mouse.pk).update(tattoo="")
    renamed_mouse.save(force_insert=True)

    for relation in Mouse._meta.related_objects:
        relation_field = relation.field

        if not (
            relation_field.many_to_one
            or relation_field.one_to_one
        ):
            continue

        relation.related_model._base_manager.filter(
            **{relation_field.name: mouse}
        ).update(
            **{relation_field.name: renamed_mouse}
        )

    Recording.mice.through.objects.filter(mouse=mouse).update(
        mouse=renamed_mouse
    )

    mouse.delete()
    return renamed_mouse


POSSIBLE_DUPLICATE_CONFIRM_FIELD = "confirm_possible_duplicate"


POSSIBLE_DUPLICATE_SIGNATURE_FIELD = "possible_duplicate_signature"


def duplicate_warning_value(value):
    if isinstance(value, Model):
        return {"model": value._meta.label_lower, "pk": str(value.pk)}
    if isinstance(value, dict):
        return {str(key): duplicate_warning_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [duplicate_warning_value(item) for item in value]
    return value


def possible_duplicate_signature(cleaned_data):
    if not cleaned_data.get("date_of_birth") or cleaned_data.get("crossing_definition") is None:
        return ""
    payload = json.dumps(
        duplicate_warning_value(cleaned_data), cls=DjangoJSONEncoder,
        sort_keys=True, separators=(",", ":"),
    )
    return sha256(payload.encode()).hexdigest()


def possible_duplicate_mouse_warning(cleaned_data):
    fingerprint = possible_duplicate_signature(cleaned_data)
    if not fingerprint:
        return None

    date_of_birth = cleaned_data["date_of_birth"]
    breeding_pair = (cleaned_data.get("breeding_pair") or "").strip()
    crossing = cleaned_data["crossing_definition"]
    identifiers = {
        (value or "").strip() for name, value in cleaned_data.items()
        if name == "tattoo" or (name.startswith("pup_") and name.endswith("_marking"))
    } - {""}
    sexes = {
        value for name, value in cleaned_data.items()
        if name == "sex" or (name.startswith("pup_") and name.endswith("_sex"))
    } - {"", None}
    marking_type = cleaned_data.get("marking_type") or "other"
    possible_duplicates = list(
        Mouse.objects.filter(
            id_status__in=[Mouse.ID_TEMPORARY, Mouse.ID_UNKNOWN],
            date_of_birth=date_of_birth, crossing_definition=crossing,
        ).select_related("protocol", "crossing_definition")
    )
    if not possible_duplicates:
        return None

    normalized_identifiers = {identifier.casefold() for identifier in identifiers}
    priority = {Mouse.ID_TEMPORARY: 0, Mouse.ID_UNKNOWN: 1}
    rows = []
    for mouse in possible_duplicates:
        rows.append({
            "mouse": mouse,
            "breeding_pair_matches": bool(breeding_pair) and mouse.breeding_pair.strip().casefold() == breeding_pair.casefold(),
            "local_identifier_matches": bool(mouse.tattoo) and mouse.tattoo.strip().casefold() in normalized_identifiers and mouse.marking_type == marking_type,
            "sex_matches": bool(mouse.sex) and mouse.sex in sexes,
        })
    rows.sort(key=lambda row: (
        -row["local_identifier_matches"], -row["breeding_pair_matches"], -row["sex_matches"],
        priority.get(row["mouse"].id_status, 1), row["mouse"].pk,
    ))
    # Bind confirmation to the current form and candidate records. Changed input
    # or newly discovered matches require the user to review a fresh warning.
    confirmation_context = {
        "form": fingerprint,
        "matches": sorted([
            [mouse.pk, mouse.id_status, mouse.breeding_pair, mouse.marking_type,
             mouse.tattoo, mouse.sex, mouse.protocol_id]
            for mouse in possible_duplicates
        ]),
    }
    shown_rows = rows[:10]
    proposed_id = cleaned_data.get("mouse_id") if not cleaned_data.get("generate_mouse_id") else ""
    if not proposed_id and not cleaned_data.get("no_id_available"):
        from ..utils import parse_mouse_numbers
        try:
            numbers = parse_mouse_numbers(cleaned_data.get("mouse_numbers", "")) if cleaned_data.get("mouse_numbers") else [""]
        except ValueError:
            numbers = []
        if len(numbers) == 1:
            proposed_id = cleaned_data.get("mouse_id_prefix", "") + numbers[0]
    for row in shown_rows:
        rename_url = reverse("lab:rename_mouse", args=[row["mouse"].pk])
        if proposed_id:
            rename_url += "?" + urlencode({"new_mouse_id": proposed_id})
        row["rename_url"] = rename_url
    return {
        "signature": signing.dumps(confirmation_context, salt="lab.possible-duplicate"),
        "confirmation_context": confirmation_context,
        "date_of_birth": date_of_birth, "breeding_pair": breeding_pair, "crossing": crossing,
        "mice": [row["mouse"] for row in shown_rows], "rows": shown_rows,
        "count": len(rows), "more_count": max(len(rows) - 10, 0),
    }


def possible_duplicate_confirmed(request, warning):
    if warning is None or request.POST.get(POSSIBLE_DUPLICATE_CONFIRM_FIELD) != "1":
        return False
    try:
        context = signing.loads(
            request.POST.get(POSSIBLE_DUPLICATE_SIGNATURE_FIELD, ""),
            salt="lab.possible-duplicate", max_age=3600,
        )
    except signing.BadSignature:
        return False
    return context == warning["confirmation_context"]


@parentage_transaction
def create_litter_mice(selected_protocol, form, *, added_by=None):
    cleaned_data = form.cleaned_data
    litter_protocol = cleaned_data.get("protocol") or selected_protocol
    mice = []
    for row in form.mouse_creation_rows():
        details = row["details"]
        mouse = Mouse.objects.create(
            mouse_id=row["mouse_id"],
            added_by=added_by,
            id_status=Mouse.ID_TEMPORARY if cleaned_data.get("no_id_available") else Mouse.ID_ASSIGNED,
            date_of_birth=details.get("date_of_birth"),
            tattoo=row["tattoo"],
            marking_type=row["marking_type"],
            sex=row["sex"],
            colour=details.get("colour", ""),
            status=details["status"],
            protocol_start_date=details.get("protocol_start_date"),
            cull_date=details.get("cull_date"),
            protocol=details["protocol"],
            severity=details.get("severity", ""),
            notes=details.get("notes", ""),
            breeding_pair=details.get("breeding_pair", ""),
        )
        sync_mouse_structured_genetics(
            mouse,
            details["crossing_definition"],
            row["genotype_values"],
            uncertainty_values=row["uncertainty_values"],
        )
        mice.append(mouse)

        for procedure_values in cleaned_data.get("procedure_drafts", []):
            Procedure.objects.create(
                mouse=mouse,
                **procedure_values,
            )

    parentage = {
        mouse.pk: {
            **form.cleaned_data.get("shared_parentage", {}),
            **form.cleaned_data.get("parentage_updates", {}).get(mouse.pk, {}),
        }
        for mouse in mice
    }
    form.parentage_warnings = list(dict.fromkeys([
        *getattr(form, "parentage_resolution_warnings", []),
        *apply_parentage(parentage),
    ]))
    return mice, litter_protocol


def mice_page(request):
    sort = request.GET.get("sort", "date_added")
    direction = request.GET.get("direction", "desc")

    if direction not in {"asc", "desc"}:
        direction = "desc"

    if sort not in mice_page_sortable_fields():
        sort = "date_added"

    filter_specs = build_mice_page_filter_specs()
    filter_values = read_filter_values(request.GET, filter_specs)

    mice = Mouse.objects.select_related("protocol", "added_by")
    mice = apply_filter_specs(mice, filter_specs, filter_values)

    mice = order_mice_page_queryset(mice, sort, direction)

    page_size = read_table_page_size(request.GET)
    paginator = Paginator(mice, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)

    return render(
        request,
        "lab/mice_page.html",
        {
            "table": build_mice_table(
                page_obj,
                sort,
                direction,
                query_params,
            ),
            "filter_form": build_filter_form(
                filter_specs,
                filter_values,
                hidden_fields=[
                    {"name": "sort", "value": sort},
                    {"name": "direction", "value": direction},
                    {"name": "per_page", "value": page_size},
                ],
                clear_url=reverse("lab:mice_page"),
            ),
            "record_count": build_record_count(page_obj, "animal"),
            "pagination": build_pagination(page_obj, query_params),
            "page_size_control": build_table_page_size_control(
                request.GET,
                page_size,
            ),
            "edit_selected_url": edit_selected_mice_all_url(),
            "add_litter_url": reverse("lab:add_litter_all"),
        },
    )


def edit_mouse(request, mouse_pk):
    """Keep bookmarked single-mouse links pointed at the unified editor."""
    mouse = get_object_or_404(Mouse.objects.select_related("protocol"), pk=mouse_pk)
    return redirect(url_with_query_params(
        edit_selected_mice_all_url(), selected_mouse=[mouse.pk],
        return_url=safe_return_url(request, procedure_protocol_url(mouse.protocol)),
        mouse_editor_tab=request.GET.get("mouse_editor_tab", "shared"),
    ))


def rename_mouse(request, mouse_pk):
    """Compatibility link for assigning an ID from duplicate-result warnings."""
    mouse = get_object_or_404(Mouse.objects.select_related("protocol"), pk=mouse_pk)
    return redirect(url_with_query_params(
        edit_selected_mice_all_url(), selected_mouse=[mouse.pk], mouse_editor_tab="ids",
        new_mouse_id=request.GET.get("new_mouse_id", ""),
        return_url=safe_return_url(request, procedure_protocol_url(mouse.protocol)),
    ))


def delete_mouse(request, mouse_pk):
    mouse = get_object_or_404(
        Mouse.objects.select_related("protocol"),
        pk=mouse_pk,
    )
    fallback_url = (
        procedure_protocol_url(mouse.protocol)
        if mouse.protocol_id
        else reverse("lab:procedure_page")
    )
    return_url = safe_return_url(request, fallback_url)
    procedure_count = Procedure.objects.filter(mouse=mouse).count()
    recording_count = recording_count_for_mice([mouse])

    if request.method == "POST":
        form = MouseDeleteConfirmForm(request.POST, mouse=mouse)

        if form.is_valid():
            mouse_id = mouse.pk

            try:
                with transaction.atomic():
                    parentage_lock()
                    locked_mouse = Mouse.objects.select_for_update().get(
                        pk=mouse_id,
                    )
                    locked_mouse.delete()
            except Mouse.DoesNotExist:
                messages.error(
                    request,
                    "Animal could not be found.",
                )
            except ProtectedError:
                messages.error(
                    request,
                    (
                        "Animal could not be deleted because another record "
                        "still protects it."
                    ),
                )
            else:
                messages.success(
                    request,
                    f"Animal {mouse_id} was deleted.",
                )

            return redirect(return_url)
    else:
        form = MouseDeleteConfirmForm(mouse=mouse)

    return render(
        request,
        "lab/delete_mouse.html",
        {
            "form": form,
            "mouse": mouse,
            "procedure_count": procedure_count,
            "recording_count": recording_count,
            "return_url": return_url,
        },
    )


def bulk_individual_rows_preview(request, form):
    from django.forms.utils import ErrorDict
    from django.template.loader import render_to_string

    form._errors = ErrorDict()
    form.cleaned_data = {}
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        if form.data.get(form.add_prefix("update_genotype")):
            form.data = form.data.copy()
            for mouse, stem, crossing, lines, calls in form.individual_row_details:
                for line in lines:
                    shared_name = genotype_field_name(line)
                    if (form.data.get(form.add_prefix("genotype_modes_present"))
                            and form.data.get(form.add_prefix(f"{shared_name}_individual"))):
                        continue
                    value = form.data.get(form.add_prefix(shared_name))
                    if value:
                        form.data[form.add_prefix(f"{stem}_{shared_name}")] = value
        return None
    return JsonResponse({
        "html": render_to_string("lab/components/bulk_individual_rows.html", {"form": form}, request=request),
    })


@require_http_methods(["GET", "HEAD", "POST"])
def view_selected_mice(request, protocol_number, licence_reference):
    protocol = protocol_by_number_or_404(protocol_number, licence_reference=licence_reference)
    return_url = safe_return_url(request, procedure_protocol_url(protocol))
    mouse_ids = selected_mouse_ids(request)
    if not mouse_ids:
        messages.error(request, "Select at least one animal before viewing.")
        return redirect(return_url)
    try:
        mice = selected_mice_for_protocol(mouse_ids, protocol)
    except Mouse.DoesNotExist:
        messages.error(request, "One or more selected animals could not be found for this protocol.")
        return redirect(return_url)
    summary_url = url_with_query_params(request.path, selected_mouse=mouse_ids, return_url=return_url)
    if request.method == "POST":
        return redirect(summary_url)
    return _render_animal_summary(
        request, mice, protocol, summary_url=summary_url, return_url=return_url,
    )


def _render_animal_summary(request, mice, protocol, *, summary_url, return_url):
    return render(request, "lab/animal_summary.html", {
        **animal_summary_context(
            request, mice, summary_url=summary_url, edit_url=edit_selected_mice_url(protocol),
        ),
        "selected_protocol": protocol,
        "return_url": return_url,
    })


def edit_selected_mice(request, protocol_number, licence_reference=None):
    selected_protocol = protocol_by_number_or_404(
        protocol_number=protocol_number,
        licence_reference=licence_reference,
    )
    return _edit_selected_mice(request, selected_protocol)


def edit_selected_mice_all(request):
    return _edit_selected_mice(request)


def _edit_selected_mice(request, selected_protocol=None):
    scoped = selected_protocol is not None
    fallback_url = procedure_protocol_url(selected_protocol) if scoped else reverse("lab:mice_page")
    return_url = safe_return_url(request, fallback_url)
    mouse_ids = selected_mouse_ids(request)

    if not mouse_ids:
        messages.error(
            request,
            "Select at least one animal before editing.",
        )
        return redirect(return_url)

    try:
        selected_mice = (
            selected_mice_for_protocol(mouse_ids, selected_protocol)
            if scoped else selected_mice_for_ids(mouse_ids)
        )
    except Mouse.DoesNotExist:
        messages.error(
            request,
            "One or more selected animals could not be found for this protocol."
            if scoped else "One or more selected animals could not be found.",
        )
        return redirect(return_url)

    if request.method == "POST" and "create_procedures" in request.POST:
        return JsonResponse({"ok": False, "error": "Add procedures to the pending list, then press Save changes."}, status=400)

    if request.method == "POST" and "existing_procedure_action" in request.POST:
        return handle_procedure_action(request, selected_mice, selected_protocol, return_url)

    can_add_bulk_procedure = all(
        mouse.protocol.allows_regulated_procedures
        for mouse in selected_mice
    )

    bulk_url = edit_selected_mice_url(selected_protocol) if scoped else edit_selected_mice_all_url()
    delete_url = delete_selected_mice_url(selected_protocol) if scoped else delete_selected_mice_all_url()
    bulk_return_url = url_with_query_params(
        bulk_url, return_url=return_url, selected_mouse=mouse_ids,
    )
    recording_return_url = url_with_query_params(bulk_return_url, mouse_editor_tab="services")
    selecting_recording = request.method == "POST" and "select_recording_destination" in request.POST
    recording_destination_form = RecordingDestinationForm(
        request.POST if selecting_recording else None, prefix="recording",
    )
    if selecting_recording and recording_destination_form.is_valid():
        experiment = recording_destination_form.cleaned_data["experiment"]
        return redirect(url_with_query_params(
            add_recording_url(experiment.project, experiment),
            return_url=recording_return_url,
            mice=recording_mice_query_value([mouse.pk for mouse in selected_mice]),
        ))

    if request.method == "POST" and "apply_bulk_edit" not in request.POST and not selecting_recording:
        query_params = [
            ("return_url", return_url),
            *[
                ("selected_mouse", mouse_id)
                for mouse_id in mouse_ids
            ],
        ]
        return redirect(f"{bulk_url}?{urlencode(query_params)}")

    if request.method == "POST" and not selecting_recording:
        form = BulkMouseUpdateForm(request.POST, selected_mice=selected_mice)

        if "preview_individual_mice" in request.POST:
            preview_response = bulk_individual_rows_preview(request, form)
            if preview_response is not None:
                return preview_response
        elif form.is_valid():
            try:
                apply_bulk_mouse_updates(selected_mice, form)
            except ValidationError as exc:
                form.parentage_errors = exc.messages
                form.add_error(None, exc)
            except IntegrityError:
                form.add_error(None, "The animal changes conflict with an existing record. No changes were saved. Review the local identifiers and shared details.")
            else:
                messages.success(request, "Changes saved successfully.")
                for warning in getattr(form, "parentage_warnings", []):
                    messages.warning(request, warning)
                renames = form.cleaned_data.get("mouse_id_renames", {})
                parts = urlsplit(return_url)
                if scoped and parts.path == reverse("lab:view_selected_mice_licence_protocol", args=[
                    selected_protocol.licence_reference, selected_protocol.protocol_number,
                ]):
                    updated_protocol = selected_mice[0].protocol
                    summary_path = reverse("lab:view_selected_mice_licence_protocol", args=[
                        updated_protocol.licence_reference, updated_protocol.protocol_number,
                    ])
                    return_url = url_with_query_params(
                        urlunsplit(parts._replace(path=summary_path)),
                        selected_mouse=[renames.get(pk, pk) for pk in mouse_ids],
                    )
                    bulk_url = edit_selected_mice_url(updated_protocol)
                if renames:
                    parts = urlsplit(return_url)
                    for old_id, new_id in renames.items():
                        if parts.path == reverse("lab:animal_summary", args=[old_id]):
                            return_url = urlunsplit(parts._replace(path=reverse("lab:animal_summary", args=[new_id])))
                    return redirect(url_with_query_params(
                        bulk_url, selected_mouse=[renames.get(mouse.pk, mouse.pk) for mouse in selected_mice],
                        return_url=return_url, mouse_editor_tab=request.POST.get("mouse_editor_tab", "ids"),
                    ))
                return redirect(return_url)
    else:
        form = BulkMouseUpdateForm(selected_mice=selected_mice)
        if len(selected_mice) == 1 and request.GET.get("new_mouse_id"):
            form.identity_rows[0]["field"].field.initial = request.GET["new_mouse_id"]

    return render(
        request,
        "lab/edit_selected_mice.html",
        {
            **editor_context(request, selected_mice, selected_protocol, return_url=bulk_return_url),
            "litter_procedure_form": BulkProcedureCreateForm(),
            "procedure_fields_enabled": can_add_bulk_procedure,
            "form": form,
            "initial_edit_tab": "individual" if len(selected_mice) > 1 else "shared",
            "selected_mice": selected_mice,
            "mouse": selected_mice[0] if len(selected_mice) == 1 else None,
            "selected_protocol": selected_protocol,
            "delete_selected_url": delete_url,
            "return_url": return_url,
            "can_add_bulk_procedure": can_add_bulk_procedure,
            "animal_recording_sections": build_animal_recording_sections(selected_mice),
            "recording_destination_form": recording_destination_form,
            "recording_destination_options": recording_destination_form.destination_options,
            "service_recording_actions": service_recording_actions_for_mice(
                selected_mice,
                recording_return_url,
            ),
            "crossing_lookup_url": reverse("lab:crossing_lookup"),
            "crossing_genotype_fields_url": reverse(
                "lab:crossing_genotype_fields"
            ),
        },
    )


def delete_selected_mice(request, protocol_number, licence_reference=None):
    selected_protocol = protocol_by_number_or_404(
        protocol_number=protocol_number,
        licence_reference=licence_reference,
    )
    return _delete_selected_mice(request, selected_protocol)


def delete_selected_mice_all(request):
    return _delete_selected_mice(request)


def _delete_selected_mice(request, selected_protocol=None):
    scoped = selected_protocol is not None
    fallback_url = (
        procedure_protocol_url(selected_protocol)
        if scoped else reverse("lab:mice_page")
    )
    return_url = safe_return_url(request, fallback_url)
    mouse_ids = selected_mouse_ids(request)
    missing_message = (
        "One or more selected animals could not be found for this protocol."
        if scoped else "One or more selected animals could not be found."
    )

    def load_selected_mice(for_update=False):
        if scoped:
            return selected_mice_for_protocol(
                mouse_ids, selected_protocol, for_update=for_update,
            )
        return selected_mice_for_ids(mouse_ids, for_update=for_update)

    if not mouse_ids:
        messages.error(
            request,
            "Select at least one animal before deleting.",
        )
        return redirect(return_url)

    try:
        selected_mice = load_selected_mice()
    except Mouse.DoesNotExist:
        messages.error(
            request,
            missing_message,
        )
        return redirect(return_url)

    if request.method == "POST" and "confirm_delete" not in request.POST:
        query_params = [
            ("return_url", return_url),
            *[
                ("selected_mouse", mouse_id)
                for mouse_id in mouse_ids
            ],
        ]
        delete_url = (
            delete_selected_mice_url(selected_protocol)
            if scoped else delete_selected_mice_all_url()
        )
        return redirect(f"{delete_url}?{urlencode(query_params)}")

    procedure_count = Procedure.objects.filter(
        mouse__in=selected_mice,
    ).count()
    recording_count = recording_count_for_mice(selected_mice)

    if request.method == "POST":
        form = BulkMouseDeleteConfirmForm(request.POST)

        if form.is_valid():
            try:
                with transaction.atomic():
                    parentage_lock()
                    locked_mice = load_selected_mice(for_update=True)
                    deleted_count = len(locked_mice)

                    for mouse in locked_mice:
                        mouse.delete()
            except Mouse.DoesNotExist:
                messages.error(
                    request,
                    missing_message,
                )
            except ProtectedError:
                messages.error(
                    request,
                    (
                        "Selected animals could not be deleted because another "
                        "record still protects at least one animal."
                    ),
                )
            else:
                messages.success(
                    request,
                    f"{deleted_count} animals were deleted.",
                )

            return redirect(return_url)
    else:
        form = BulkMouseDeleteConfirmForm()

    return render(
        request,
        "lab/delete_selected_mice.html",
        {
            "form": form,
            "selected_mice": selected_mice,
            "selected_protocol": selected_protocol,
            "procedure_count": procedure_count,
            "recording_count": recording_count,
            "return_url": return_url,
        },
    )


def protocol_assignment_message(mouse_count, protocol):
    if protocol is None:
        return f"{mouse_count} animals were added with no protocol."

    return (
        f"{mouse_count} animals were added to protocol "
        f"{protocol.protocol_number}."
    )


def add_mouse(request, protocol_number=None, licence_reference=None):
    """Compatibility redirect; all animal creation uses the same form."""
    protocol = protocol_by_number_or_404(protocol_number, licence_reference) if protocol_number is not None else None
    destination = (reverse("lab:add_litter_licence_protocol", args=[protocol.licence_reference, protocol.protocol_number])
                   if protocol is not None else reverse("lab:add_litter_all"))
    return redirect(url_with_query_params(
        destination,
        return_url=safe_return_url(request, procedure_protocol_url(protocol) if protocol is not None else reverse("lab:mice_page")),
        return_with_mouse=request.GET.get("return_with_mouse", ""),
    ))


def add_litter(request, protocol_number=None, licence_reference=None):
    selected_protocol = (
        protocol_by_number_or_404(
            protocol_number=protocol_number,
            licence_reference=licence_reference,
        )
        if protocol_number is not None
        else None
    )
    fallback_url = (
        procedure_protocol_url(selected_protocol)
        if selected_protocol is not None
        else reverse("lab:mice_page")
    )
    return_url = safe_return_url(request, fallback_url)

    return_with_mouse = bool(request.POST.get("return_with_mouse") or request.GET.get("return_with_mouse"))
    possible_duplicate_warning = None

    if request.method == "POST":
        form = LitterCreateForm(
            request.POST,
            selected_protocol=selected_protocol,
        )

        preview_only = "preview_mice" in request.POST or "apply_shared_genotype" in request.POST
        if preview_only and request.headers.get("X-Requested-With") == "XMLHttpRequest":
            from django.forms.utils import ErrorDict
            from django.template.loader import render_to_string

            form._errors = ErrorDict()
            form.cleaned_data = {}
            if form.numbers_error:
                form.add_error("mouse_numbers", form.numbers_error)
            errors = list(form.errors.get("mouse_numbers", []))
            if form.data.get("no_id_available") and not form.data.get("mouse_id_prefix"):
                errors.append("Enter a birth date and breeding pair, and select a crossing to generate the prefix.")
            return JsonResponse({
                "html": render_to_string("lab/components/litter_mouse_rows.html", {"form": form}, request=request),
                "genotype_html": render_to_string("lab/components/litter_shared_genotypes.html", {"form": form}, request=request),
                "detail_overrides_html": render_to_string("lab/components/individual_animal_overrides.html", {"form": form}, request=request),
                "parent_overrides_html": render_to_string("lab/components/litter_parent_overrides.html", {"form": form}, request=request),
                "mouse_count": len(form.mouse_rows),
                "prefix": form.data.get("mouse_id_prefix", ""),
                "errors": errors,
            })

        valid = form.is_valid()
        if not preview_only:
            possible_duplicate_warning = possible_duplicate_mouse_warning(form.cleaned_data)
        if valid and not preview_only:
            if (
                possible_duplicate_warning is None
                or possible_duplicate_confirmed(
                    request,
                    possible_duplicate_warning,
                )
            ):
                try:
                    with transaction.atomic():
                        mice, litter_protocol = create_litter_mice(
                            selected_protocol,
                            form,
                            added_by=request.user,
                        )
                except ValidationError as exc:
                    form.parentage_errors = exc.messages
                    form.add_error(None, exc)
                except IntegrityError:
                    possible_duplicate_warning = None
                    form.add_error(
                        None,
                        (
                            "One or more Animal IDs or local identifiers already exist. "
                            "Review the individual animal rows."
                        ),
                    )
                else:
                    messages.success(
                        request,
                        protocol_assignment_message(len(mice), litter_protocol),
                    )

                    for warning in getattr(form, "parentage_warnings", []):
                        messages.warning(request, warning)
                    if return_with_mouse:
                        mouse_ids = selected_recording_mouse_ids_from_url(return_url)
                        mouse_ids.extend(mouse.pk for mouse in mice if mouse.pk not in mouse_ids)
                        destination = url_with_query_params(return_url, mice=recording_mice_query_value(mouse_ids), mouse=None)
                    elif selected_protocol is None or litter_protocol == selected_protocol:
                        destination = return_url
                    else:
                        destination = procedure_protocol_url(litter_protocol)
                    return redirect(url_with_query_params(
                        reverse("lab:view_selected_mice_all"),
                        selected_mouse=[mouse.pk for mouse in mice],
                        return_url=destination,
                    ))
    else:
        form = LitterCreateForm(selected_protocol=selected_protocol)
    can_add_litter_procedure = (
        selected_protocol is not None
        and selected_protocol.allows_regulated_procedures
    )
    protocol_permissions = Protocol.objects.values_list(
        "protocol_id",
        "allows_regulated_procedures",
    )
    protocol_procedure_permissions = {
        str(protocol_id): allows_regulated_procedures
        for protocol_id, allows_regulated_procedures in protocol_permissions
    }
    context = {
        "form": form,
        "return_with_mouse": return_with_mouse,
        "selected_protocol": selected_protocol,
        "return_url": return_url,
        "can_add_litter_procedure": can_add_litter_procedure,
        "litter_procedure_form": BulkProcedureCreateForm(),
        "protocol_procedure_permissions": protocol_procedure_permissions,
        "possible_duplicate_warning": possible_duplicate_warning,
        "crossing_lookup_url": reverse("lab:crossing_lookup"),
        "crossing_genotype_fields_url": reverse(
            "lab:crossing_genotype_fields"
        ),
    }

    return render(
        request,
        "lab/add_animals.html",
        context,
    )


def mouse_id_preview(request):
    date_string = request.GET.get("date_of_birth")
    breeding_pair = request.GET.get("breeding_pair", "")
    tattoo = request.GET.get("tattoo", "")
    crossing_id = request.GET.get("crossing_definition", "")

    if not date_string or not breeding_pair or not tattoo or not crossing_id:
        return JsonResponse({})

    try:
        date_of_birth = datetime.strptime(
            date_string,
            "%Y-%m-%d",
        ).date()
    except ValueError:
        return JsonResponse({})

    crossing = (
        Crossing.objects
        .filter(pk=crossing_id, active=True)
        .prefetch_related("lines")
        .first()
    )

    if crossing is None:
        return JsonResponse({})

    base_id = mouse_id_base(date_of_birth, breeding_pair, crossing, tattoo)
    final_id = generate_mouse_id(
        date_of_birth,
        breeding_pair,
        crossing,
        tattoo,
    )

    return JsonResponse(
        {
            "base_id": base_id,
            "final_id": final_id,
        }
    )


def mouse_lookup(request):
    mouse_query = (request.GET.get("q") or "").strip()

    if not mouse_query:
        return JsonResponse({"results": []})

    mice = (
        Mouse.objects
        .exclude(pk__in=request.GET.getlist("exclude"))
        .filter(mouse_id__icontains=mouse_query)
        .order_by("mouse_id")[:10]
    )

    payload = {"results": [{"id": mouse.pk, "label": mouse.mouse_id} for mouse in mice]}
    if request.GET.get("resolve_exact") == "1":
        # Exact resolution must not depend on the truncated suggestion list.
        exact = list(Mouse.objects.filter(pk__iexact=mouse_query).order_by("pk").values_list("pk", flat=True)[:2])
        payload["exact_match"] = {"id": exact[0], "label": exact[0]} if len(exact) == 1 else None
        payload["ambiguous"] = len(exact) > 1
    return JsonResponse(payload)


@require_http_methods(["GET", "HEAD"])
def animal_summary(request, mouse_pk):
    mouse = get_object_or_404(Mouse.objects.select_related("protocol"), pk=mouse_pk)
    return_url = safe_return_url(request, procedure_protocol_url(mouse.protocol))
    return _render_animal_summary(
        request, [mouse], mouse.protocol,
        summary_url=url_with_query_params(request.path, return_url=return_url),
        return_url=return_url,
    )
