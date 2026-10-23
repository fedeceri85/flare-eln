'Procedures views and helpers.'

from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..forms import ProcedureEditForm
from ..models import (
    Mouse,
    Procedure,
    ProcedureType,
    Protocol,
)
from ..ui import (
    MOUSE_FILTER_SPECS,
    apply_filter_specs,
    build_filter_form,
    build_pagination,
    build_procedure_matrix_table,
    build_record_count,
    build_table_page_size_control,
    read_filter_values,
    read_table_page_size,
    sortable_fields,
)
from .common import (
    apply_current_year_filter,
    apply_severity_exceeded_filter,
    first_protocol_or_404,
    licence_references,
    procedure_licence_url,
    procedure_protocol_url,
    protocol_by_number_or_404,
    redirect_preserving_query,
    safe_return_url,
    severity_exceeded_filter_enabled,
    show_current_year_enabled,
)


def procedure_page(request, licence_reference=None, protocol_number=None):
    all_licence_references = licence_references()

    if not all_licence_references:
        return render(
            request,
            "lab/procedure_page.html",
            {
                "licence_references": all_licence_references,
                "selected_licence_reference": None,
                "protocols": Protocol.objects.none(),
                "selected_protocol": None,
            },
        )

    if licence_reference is None:
        if protocol_number is not None:
            selected_protocol = protocol_by_number_or_404(protocol_number)
            return redirect_preserving_query(
                request,
                procedure_protocol_url(selected_protocol),
            )

        return redirect_preserving_query(
            request,
            procedure_licence_url(all_licence_references[0]),
        )

    protocols = Protocol.objects.filter(
        licence_reference=licence_reference,
    ).order_by(
        "protocol_number",
        "protocol_id",
    )

    if protocol_number is None:
        selected_protocol = first_protocol_or_404(protocols)
    else:
        selected_protocol = protocol_by_number_or_404(
            protocol_number,
            licence_reference=licence_reference,
        )

    sort = request.GET.get("sort", "protocol_start_date")
    direction = request.GET.get("direction", "desc")

    if direction not in {"asc", "desc"}:
        direction = "desc"

    if sort not in sortable_fields():
        sort = "protocol_start_date"

    order_by_field = sort if direction == "asc" else f"-{sort}"
    filter_values = read_filter_values(request.GET, MOUSE_FILTER_SPECS)
    current_year = timezone.localdate().year

    mice = (
        Mouse.objects
        .filter(protocol=selected_protocol)
        .select_related("protocol", "added_by")
        .prefetch_related("procedures__procedure_type")
    )

    mice = apply_filter_specs(mice, MOUSE_FILTER_SPECS, filter_values)

    if show_current_year_enabled(filter_values):
        mice = apply_current_year_filter(mice, current_year)

    if severity_exceeded_filter_enabled(filter_values):
        mice = apply_severity_exceeded_filter(mice)

    procedure_types = (
        ProcedureType.objects
        .filter(procedures__mouse__in=mice)
        .distinct()
        .order_by("name")
    )

    if sort == "protocol_start_date":
        date_order = F(sort).desc(nulls_last=True) if direction == "desc" else F(sort).asc(nulls_last=True)
        mice = mice.order_by(date_order, "mouse_id")
    else:
        mice = mice.order_by(order_by_field, "mouse_id")

    page_size = read_table_page_size(request.GET)
    paginator = Paginator(mice, page_size)

    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)

    context = {
        "licence_references": all_licence_references,
        "selected_licence_reference": licence_reference,
        "protocols": protocols,
        "selected_protocol": selected_protocol,
        "table": build_procedure_matrix_table(
            page_obj,
            procedure_types,
            sort,
            direction,
            query_params,
        ),
        "filter_form": build_filter_form(
            MOUSE_FILTER_SPECS,
            filter_values,
            hidden_fields=[
                {"name": "sort", "value": sort},
                {"name": "direction", "value": direction},
                {"name": "per_page", "value": page_size},
            ],
            clear_url=procedure_protocol_url(selected_protocol),
        ),
        "current_year": current_year,
        "record_count": build_record_count(page_obj, "animal"),
        "pagination": build_pagination(page_obj, query_params),
        "page_size_control": build_table_page_size_control(
            request.GET,
            page_size,
        ),
    }

    return render(
        request,
        "lab/procedure_page.html",
        context,
    )


def edit_mouse_procedure(request, mouse_pk, procedure_pk):
    mouse = get_object_or_404(
        Mouse.objects.select_related("protocol"),
        pk=mouse_pk,
    )
    procedure = get_object_or_404(
        Procedure.objects.select_related("procedure_type"),
        pk=procedure_pk,
        mouse=mouse,
    )
    fallback_url = (
        procedure_protocol_url(mouse.protocol)
        if mouse.protocol_id
        else reverse("lab:procedure_page")
    )
    return_url = safe_return_url(request, fallback_url)
    edit_mouse_url = reverse("lab:edit_mouse", args=[mouse.pk])
    cancel_url = f"{edit_mouse_url}?{urlencode({'return_url': return_url})}"

    if request.method == "POST":
        form = ProcedureEditForm(request.POST, instance=procedure)

        if form.is_valid():
            with transaction.atomic():
                form.save()

            messages.success(
                request,
                f"Procedure was updated for animal {mouse.mouse_id}.",
            )
            return redirect(cancel_url)
    else:
        form = ProcedureEditForm(instance=procedure)

    return render(
        request,
        "lab/edit_mouse_procedure.html",
        {
            "form": form,
            "mouse": mouse,
            "procedure": procedure,
            "return_url": return_url,
            "cancel_url": cancel_url,
        },
    )


@require_POST
def delete_mouse_procedure(request, mouse_pk, procedure_pk):
    mouse = get_object_or_404(Mouse, pk=mouse_pk)
    fallback_url = (
        procedure_protocol_url(mouse.protocol)
        if mouse.protocol_id
        else reverse("lab:procedure_page")
    )
    return_url = safe_return_url(request, fallback_url)
    procedure = get_object_or_404(
        Procedure,
        pk=procedure_pk,
        mouse=mouse,
    )
    procedure_name = str(procedure.procedure_type)

    with transaction.atomic():
        procedure.delete()

    messages.success(
        request,
        f"Procedure {procedure_name} was deleted for animal {mouse.mouse_id}.",
    )
    edit_url = reverse("lab:edit_mouse", args=[mouse.pk])
    return redirect(
        f"{edit_url}?{urlencode({'return_url': return_url})}"
    )
