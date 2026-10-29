"""Independent preview/save workflow for existing procedure objects."""
from datetime import date
from urllib.parse import urlencode

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.urls import reverse

from .forms import ExistingProcedureBulkForm
from .models import Mouse, Procedure, ProcedureType

SALT = "lab.existing-procedure-bulk.v1"
FIELDS = ("procedure_type_id", "date", "lab_member", "lab_book_page")


def procedure_queryset(mice, protocol=None):
    queryset = Procedure.objects.filter(mouse_id__in=[mouse.pk for mouse in mice])
    if protocol is not None:
        queryset = queryset.filter(mouse__protocol=protocol)
    return queryset


def normalized_updates(updates):
    return {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in updates.items()
    }


def snapshot_state(scope, updates, records, deleting=False):
    record_fields = (*FIELDS, "notes") if deleting else FIELDS
    return {
        "scope": scope,
        "operation": "delete" if deleting else "update",
        "updates": normalized_updates(updates),
        "records": [
            {"id": record.pk, "mouse": record.mouse_id,
             **normalized_updates({name: getattr(record, name) for name in record_fields})}
            for record in records
        ],
    }


def procedure_scope(request, mice, protocol):
    return {
        "mice": sorted(mouse.pk for mouse in mice),
        "protocol": protocol.pk if protocol else None,
        "user": request.user.pk,
    }


def editor_context(request, mice, protocol=None, form=None, preview=None, message="", return_url=""):
    queryset = procedure_queryset(mice, protocol)
    form = form if form is not None else ExistingProcedureBulkForm(procedures=queryset)
    selected = set(map(str, form["selected_procedures"].value() or []))
    rows = []
    scope = procedure_scope(request, mice, protocol)
    records = queryset.select_related("procedure_type").order_by(
        "mouse_id", F("date").desc(nulls_last=True), "-procedure_id",
    )
    for record in records:
        edit_url = reverse("lab:edit_mouse_procedure", args=[record.mouse_id, record.pk])
        rows.append({
            "record": record,
            "selected": str(record.pk) in selected,
            "delete_snapshot": signing.dumps(snapshot_state(scope, {}, [record], deleting=True), salt=SALT, compress=True),
            "edit_url": edit_url + "?" + urlencode({"return_url": return_url or request.get_full_path()}),
        })
    return {
        "existing_procedure_form": form,
        "existing_procedure_rows": rows,
        "procedure_selected_mice": mice,
        "procedure_preview": preview,
        "procedure_message": message,
        "procedure_return_url": return_url,
    }


def preview_rows(form, records, updates):
    types = dict(ProcedureType.objects.values_list("pk", "name"))
    rows = []
    for record in records:
        changes = []
        for key, after in updates.items():
            before = getattr(record, key)
            if key == "procedure_type_id":
                before, after = types.get(before, before), types.get(after, after)
                label = "Procedure type"
            else:
                label = form.fields[key].label or key.replace("_", " ").capitalize()
            changes.append({"label": label, "before": before, "after": after})
        rows.append({"record": record, "changes": changes})
    return rows


def save_preview(token, mice, protocol, ids, scope, updates, deleting=False, delete_snapshots=None):
    snapshot = None if deleting else signing.loads(token, salt=SALT, max_age=3600)
    with transaction.atomic():
        # Lock mice too so a change of protocol cannot race the scope comparison.
        locked_mice = list(
            Mouse.objects.select_for_update().filter(pk__in=scope["mice"]).order_by("pk")
        )
        if len(locked_mice) != len(mice) or (
            protocol and any(mouse.protocol_id != protocol.pk for mouse in locked_mice)
        ):
            raise signing.BadSignature("Mouse scope changed")
        records = list(
            procedure_queryset(mice, protocol).select_for_update().filter(pk__in=ids).order_by("pk")
        )
        if len(records) != len(ids):
            raise signing.BadSignature("Records changed")
        if deleting:
            for record in records:
                signed_record = signing.loads((delete_snapshots or {}).get(record.pk, ""), salt=SALT, max_age=3600)
                if signed_record != snapshot_state(scope, {}, [record], deleting=True):
                    raise signing.BadSignature("Record changed")
            Procedure.objects.filter(pk__in=ids).delete()
        else:
            if snapshot != snapshot_state(scope, updates, records):
                raise signing.BadSignature("Records changed")
            Procedure.objects.filter(pk__in=ids).update(**updates)


def handle_procedure_action(request, mice, protocol=None, return_url=""):
    queryset = procedure_queryset(mice, protocol)
    action = request.POST.get("existing_procedure_action")
    deleting = action == "delete"
    form = ExistingProcedureBulkForm(request.POST, procedures=queryset, deleting=deleting)
    preview = None
    message = ""
    status = 400
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        form.add_error(None, "Use the procedure controls with JavaScript enabled, or edit an individual record.")
    elif action not in ("preview", "save", "delete"):
        form.add_error(None, "Unknown procedure action.")
    elif deleting and request.POST.get("existing_procedures-confirm_delete") != "1":
        form.add_error(None, "Confirm deletion before deleting selected procedures.")
    elif form.is_valid():
        updates = {} if deleting else form.updates()
        ids = sorted(record.pk for record in form.cleaned_data["selected_procedures"])
        scope = procedure_scope(request, mice, protocol)
        if action == "preview":
            records = list(queryset.select_related("procedure_type").filter(pk__in=ids).order_by("pk"))
            token = signing.dumps(snapshot_state(scope, updates, records, deleting), salt=SALT, compress=True)
            form.data = form.data.copy()
            form.data[form.add_prefix("snapshot")] = token
            preview = preview_rows(form, records, updates)
            status = 200
        else:
            try:
                delete_snapshots = {
                    pk: request.POST.get(f"existing_procedures-delete_snapshot_{pk}", "") for pk in ids
                } if deleting else None
                save_preview(form.cleaned_data["snapshot"], mice, protocol, ids, scope, updates, deleting, delete_snapshots)
                verb = "Deleted" if deleting else "Updated"
                message = f"{verb} {len(ids)} procedure records. Animal changes have not been saved."
                form = ExistingProcedureBulkForm(procedures=queryset)
                status = 200
            except (signing.BadSignature, ValueError, TypeError):
                form.add_error(None, (
                    "The selected procedures changed or this table expired. Review the refreshed list and confirm deletion again."
                    if deleting else "This preview expired or the selected records or changes have changed. Preview again before continuing."
                ))
                status = 409
            except IntegrityError:
                form.add_error(None, (
                    "No procedures were deleted. Review the refreshed list and confirm deletion again."
                    if deleting else "The procedure request could not be completed. No procedure changes were made. Preview again."
                ))
                status = 409
    context = editor_context(request, mice, protocol, form, preview, message, return_url)
    return JsonResponse({
        "ok": status == 200,
        "html": render_to_string("lab/components/existing_procedure_editor.html", context, request=request),
    }, status=status)
