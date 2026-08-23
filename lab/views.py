from copy import deepcopy
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.db.models.deletion import ProtectedError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .forms import (
    BulkMouseLineCreateForm,
    BulkMouseUpdateForm,
    BulkMouseDeleteConfirmForm,
    CrossingCreateForm,
    ExperimentForm,
    HomeOfficeReturnForm,
    LitterCreateForm,
    MouseDeleteConfirmForm,
    MouseEditForm,
    MouseForm,
    MouseRenameForm,
    crossing_choice_label,
    genotype_field_name,
    normalize_choice_values,
    ProcedureCreateForm,
    ProcedureEditForm,
    ProjectForm,
    RecordingForm,
    sync_mouse_structured_genetics,
)
from .models import (
    Crossing,
    DEFAULT_SPECIES_NAME,
    Experiment,
    Mouse,
    MAX_SEVERITY_CHOICES,
    MouseLine,
    Procedure,
    ProcedureType,
    Project,
    Protocol,
    Recording,
    RecordingField,
    RecordingType,
    SERVICES_PROJECT_DESCRIPTION,
    SERVICES_PROJECT_NAME,
    Species,
    ZYGOSITY_CHOICES,
)
from .ui import (
    CROSSING_FILTER_AUTOCOMPLETE,
    EXPERIMENT_FILTER_SPECS,
    MOUSE_FILTER_SPECS,
    PROJECT_FILTER_SPECS,
    RECORDING_FILTER_SPECS,
    apply_filter_specs,
    build_filter_form,
    build_experiments_table,
    build_home_cards,
    build_mice_page_filter_specs,
    build_mouse_selector_table,
    build_mice_table,
    build_mouse_record_sections,
    build_pagination,
    build_projects_table,
    build_procedure_matrix_table,
    build_record_count,
    build_recordings_table,
    build_service_experiment_cards,
    build_table_page_size_control,
    experiment_page_sortable_fields,
    mice_page_sortable_fields,
    project_page_sortable_fields,
    read_filter_values,
    read_table_page_size,
    sortable_fields,
)
from .utils import generate_mouse_id


def safe_return_url(request, fallback_url):
    return_url = (
        request.POST.get("return_url")
        or request.GET.get("return_url")
        or ""
    )

    if return_url and url_has_allowed_host_and_scheme(
        return_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return return_url

    return fallback_url


def url_with_query_params(url, **params):
    parts = urlsplit(url)
    query_params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in params
    ]

    for key, value in params.items():
        if value is None:
            continue

        if isinstance(value, (list, tuple)):
            query_params.extend(
                (key, item)
                for item in value
            )
        else:
            query_params.append((key, value))

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query_params),
            parts.fragment,
        )
    )


def selected_mouse_ids(request):
    seen = set()
    mouse_ids = []
    raw_mouse_ids = request.POST.getlist("selected_mouse")

    if not raw_mouse_ids and request.method != "POST":
        raw_mouse_ids = request.GET.getlist("selected_mouse")

    for mouse_id in raw_mouse_ids:
        if not mouse_id or mouse_id in seen:
            continue

        seen.add(mouse_id)
        mouse_ids.append(mouse_id)

    return mouse_ids


def selected_recording_mouse_ids_from_url(url):
    parts = urlsplit(url)
    query_params = dict(parse_qsl(parts.query, keep_blank_values=True))
    mouse_ids = RecordingForm.split_mouse_ids(query_params.get("mice", ""))
    legacy_mouse_id = query_params.get("mouse", "").strip()

    if legacy_mouse_id and legacy_mouse_id not in mouse_ids:
        mouse_ids.append(legacy_mouse_id)

    return mouse_ids


def selected_recording_mouse_ids_from_request(request):
    mouse_ids = RecordingForm.split_mouse_ids(request.GET.get("mice", ""))
    legacy_mouse_id = (request.GET.get("mouse") or "").strip()

    if legacy_mouse_id and legacy_mouse_id not in mouse_ids:
        mouse_ids.append(legacy_mouse_id)

    return mouse_ids


def recording_mice_query_value(mouse_ids):
    return ",".join(mouse_ids)


def selected_mice_for_protocol(mouse_ids, selected_protocol, for_update=False):
    mice = Mouse.objects.all()

    if for_update:
        mice = mice.select_for_update()
    else:
        mice = mice.select_related("protocol")

    mice = mice.filter(pk__in=mouse_ids, protocol=selected_protocol)
    mice_by_id = {
        mouse.pk: mouse
        for mouse in mice
    }
    missing_ids = [
        mouse_id
        for mouse_id in mouse_ids
        if mouse_id not in mice_by_id
    ]

    if missing_ids:
        raise Mouse.DoesNotExist(
            "One or more selected mice could not be found for this protocol."
        )

    return [
        mice_by_id[mouse_id]
        for mouse_id in mouse_ids
    ]


def selected_mice_for_ids(mouse_ids, for_update=False):
    mice = Mouse.objects.all()

    if for_update:
        mice = mice.select_for_update()
    else:
        mice = mice.select_related("protocol")

    mice = mice.filter(pk__in=mouse_ids)
    mice_by_id = {
        mouse.pk: mouse
        for mouse in mice
    }
    missing_ids = [
        mouse_id
        for mouse_id in mouse_ids
        if mouse_id not in mice_by_id
    ]

    if missing_ids:
        raise Mouse.DoesNotExist(
            "One or more selected mice could not be found."
        )

    return [
        mice_by_id[mouse_id]
        for mouse_id in mouse_ids
    ]


def append_text(existing_text, text_to_append):
    existing_text = (existing_text or "").rstrip()
    text_to_append = text_to_append.strip()

    if existing_text:
        return f"{existing_text}\n\n{text_to_append}"

    return text_to_append


def apply_bulk_mouse_updates(mice, form):
    mouse_updates = form.selected_mouse_updates()
    structured_genetics_update = form.selected_structured_genetics_update()
    notes_to_append = form.notes_to_append()
    procedure_type = form.cleaned_data.get("procedure_type")
    procedure_updates = form.selected_procedure_updates()

    for mouse in mice:
        changed_mouse_fields = []

        for field_name, value in mouse_updates.items():
            setattr(mouse, field_name, value)
            changed_mouse_fields.append(field_name)

        if notes_to_append:
            mouse.notes = append_text(mouse.notes, notes_to_append)
            changed_mouse_fields.append("notes")

        if changed_mouse_fields:
            mouse.save(update_fields=changed_mouse_fields)

        if structured_genetics_update is not None:
            crossing, genotype_values, preserve_missing = (
                structured_genetics_update
            )
            sync_mouse_structured_genetics(
                mouse,
                crossing,
                genotype_values,
                preserve_missing=preserve_missing,
            )

        if procedure_type is None:
            continue

        procedure_values = {
            "mouse": mouse,
            "procedure_type": procedure_type,
        }

        for field_name, value in procedure_updates.items():
            procedure_values[field_name] = value

        Procedure.objects.create(**procedure_values)


def create_mouse_procedure(mouse, form):
    procedure = form.save(commit=False)
    procedure.mouse = mouse
    procedure.save()
    return procedure


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


def recording_count_for_mice(mice):
    mouse_ids = [
        mouse.pk
        for mouse in mice
    ]

    if not mouse_ids:
        return 0

    return (
        Recording.objects
        .filter(mice__mouse_id__in=mouse_ids)
        .distinct()
        .count()
    )


def create_litter_mice(selected_protocol, form):
    cleaned_data = form.cleaned_data
    litter_protocol = cleaned_data.get("protocol") or selected_protocol
    use_progressive_mouse_ids = form.uses_progressive_mouse_ids()
    progressive_mouse_ids = (
        form.progressive_mouse_ids()
        if use_progressive_mouse_ids
        else []
    )
    mice = []
    procedure_type = cleaned_data.get("procedure_type")

    procedure_values = None

    if procedure_type is not None:
        procedure_values = {
            "procedure_type": procedure_type,
            "date": cleaned_data.get("procedure_date"),
            "lab_member": cleaned_data.get("procedure_lab_member", ""),
            "lab_book_page": cleaned_data.get("procedure_lab_book_page", ""),
        }

    for offset, tattoo in enumerate(form.tattoos()):
        if use_progressive_mouse_ids:
            mouse_id = progressive_mouse_ids[offset]
        else:
            mouse_id = generate_mouse_id(
                cleaned_data["date_of_birth"],
                cleaned_data["breeding_pair"],
                tattoo,
            )

        mouse = Mouse.objects.create(
            mouse_id=mouse_id,
            date_of_birth=cleaned_data.get("date_of_birth"),
            tattoo=tattoo,
            sex=cleaned_data.get("sex", ""),
            status=cleaned_data.get("status", ""),
            protocol_start_date=cleaned_data.get("protocol_start_date"),
            cull_date=cleaned_data.get("cull_date"),
            protocol=litter_protocol,
            severity=cleaned_data.get("severity", ""),
            notes=cleaned_data.get("notes", ""),
            breeding_pair=cleaned_data.get("breeding_pair", ""),
        )
        sync_mouse_structured_genetics(
            mouse,
            cleaned_data["crossing_definition"],
            cleaned_data.get("genotype_values", {}),
        )
        mice.append(mouse)

        if procedure_values is not None:
            Procedure.objects.create(
                mouse=mouse,
                **procedure_values,
            )

    return mice, litter_protocol


def show_current_year_enabled(filter_values):
    return filter_values.get("show_current_year", "0") not in {
        "",
        "0",
        "false",
        "False",
        "off",
    }


def apply_current_year_filter(queryset, year):
    return queryset.filter(
        Q(date_of_birth__year=year)
        | Q(protocol_start_date__year=year)
        | Q(cull_date__year=year)
        | Q(procedures__date__year=year)
    ).distinct()


def severity_exceeded_filter_enabled(filter_values):
    return filter_values.get("severity_exceeded") not in {
        "",
        "0",
        "false",
        "False",
        "off",
    }


def apply_severity_exceeded_filter(queryset):
    return queryset.filter(
        Q(
            severity="Moderate",
            protocol__max_severity="Mild",
        )
        | Q(
            severity="Severe",
            protocol__max_severity__in=["Mild", "Moderate"],
        )
    )


HOME_OFFICE_RETURN_REPORT = "home_office_return"
HOME_OFFICE_WARNING = (
    "Mice are assigned to this return based on protocol start date, "
    "not protocol end date."
)
HOME_OFFICE_SEVERITY_KEYS = {
    severity_value: severity_value.lower()
    for severity_value, _ in MAX_SEVERITY_CHOICES
}
HOME_OFFICE_CROSSING_TYPE_LABELS = [
    label
    for _, label in Mouse.CROSSING_CHOICES
]
HOME_OFFICE_UNKNOWN_CROSSING_TYPE = "Unknown"
LEGACY_HOME_OFFICE_CROSSING_TYPE_LABELS = {
    "Double_triple": "Multiple",
}


def empty_home_office_counts():
    return {
        "total": 0,
        "mild": 0,
        "moderate": 0,
        "severe": 0,
        "not_set": 0,
        "exceeded": 0,
    }


def empty_crossing_type_counts():
    return {
        label: 0
        for label in [
            *HOME_OFFICE_CROSSING_TYPE_LABELS,
            HOME_OFFICE_UNKNOWN_CROSSING_TYPE,
        ]
    }


def crossing_type_label(mouse):
    if mouse.crossing_type in LEGACY_HOME_OFFICE_CROSSING_TYPE_LABELS:
        return LEGACY_HOME_OFFICE_CROSSING_TYPE_LABELS[mouse.crossing_type]

    return mouse.get_crossing_type_display() or HOME_OFFICE_UNKNOWN_CROSSING_TYPE


def increment_crossing_type_counts(counts, mouse):
    label = crossing_type_label(mouse)
    counts[label] = counts.get(label, 0) + 1


def crossing_type_rows(counts):
    return [
        {
            "label": label,
            "count": counts.get(label, 0),
        }
        for label in [
            *HOME_OFFICE_CROSSING_TYPE_LABELS,
            HOME_OFFICE_UNKNOWN_CROSSING_TYPE,
        ]
    ]


def add_mouse_to_home_office_counts(counts, mouse):
    counts["total"] += 1
    severity_key = HOME_OFFICE_SEVERITY_KEYS.get(mouse.severity)

    if severity_key:
        counts[severity_key] += 1
    else:
        counts["not_set"] += 1

    if mouse.severity_exceeded:
        counts["exceeded"] += 1


def build_report_cards():
    return [
        {
            "title": "Mice",
            "description": "Browse, filter and manage all mouse records.",
            "url": reverse("lab:mice_page"),
        },
        {
            "title": "Home Office Return",
            "description": (
                "Generate project licence return counts by protocol "
                "procedure, and crossing type."
            ),
            "url": (
                f"{reverse('lab:reports_page')}"
                f"?report={HOME_OFFICE_RETURN_REPORT}"
            ),
        },
    ]


def build_home_office_return_text(report):
    lines = [
        "Home Office Return",
        f"Project licence: {report['licence_reference']}",
        (
            "Date range: "
            f"{report['start_date']:%d/%m/%Y} to "
            f"{report['end_date']:%d/%m/%Y}"
        ),
        "",
        f"Warning: {report['warning']}",
        "",
        "Total numbers",
        f"Mice under procedure: {report['total']['total']}",
        f"Mild: {report['total']['mild']}",
        f"Moderate: {report['total']['moderate']}",
        f"Severe: {report['total']['severe']}",
        f"Severity not set: {report['total']['not_set']}",
        f"Surpassed severity threshold: {report['total']['exceeded']}",
        "",
        "Total by crossing type",
    ]

    for row in report["total_crossing_type_rows"]:
        lines.append(f"{row['label']}: {row['count']}")

    lines.extend([
        "",
        "Breakdown by protocol",
    ])

    for row in report["protocol_rows"]:
        max_severity = row["max_severity"] or "Not set"
        lines.extend(
            [
                "",
                row["protocol_label"],
                f"Max severity: {max_severity}",
                f"Mice under procedure: {row['total']}",
                f"Mild: {row['mild']}",
                f"Moderate: {row['moderate']}",
                f"Severe: {row['severe']}",
                f"Severity not set: {row['not_set']}",
                f"Surpassed severity threshold: {row['exceeded']}",
                "By crossing type:",
            ]
        )
        lines.extend(
            f"{crossing_type_row['label']}: {crossing_type_row['count']}"
            for crossing_type_row in row["crossing_type_rows"]
        )

    lines.extend([
        "",
        "Breakdown by procedure",
    ])

    for row in report["procedure_rows"]:
        lines.extend(
            [
                "",
                row["procedure_label"],
                f"Mice under procedure: {row['total']}",
                f"Mild: {row['mild']}",
                f"Moderate: {row['moderate']}",
                f"Severe: {row['severe']}",
                f"Severity not set: {row['not_set']}",
                f"Surpassed severity threshold: {row['exceeded']}",
            ]
        )

    return "\n".join(lines)


def build_home_office_return_report(
    licence_reference,
    start_date,
    end_date,
    output_mode,
):
    protocols = list(
        Protocol.objects.filter(
            licence_reference=licence_reference,
            allows_regulated_procedures=True,
        ).order_by(
            "protocol_number",
            "protocol_id",
        )
    )
    total_counts = empty_home_office_counts()
    total_crossing_type_counts = empty_crossing_type_counts()
    counts_by_protocol = {
        protocol.pk: empty_home_office_counts()
        for protocol in protocols
    }
    crossing_type_counts_by_protocol = {
        protocol.pk: empty_crossing_type_counts()
        for protocol in protocols
    }
    mice = list(
        Mouse.objects.select_related("protocol")
        .prefetch_related("procedures__procedure_type")
        .filter(
            protocol__licence_reference=licence_reference,
            protocol_start_date__gte=start_date,
            protocol_start_date__lte=end_date,
            protocol__allows_regulated_procedures=True,
        )
        .order_by("protocol__protocol_number", "mouse_id")
    )
    counts_by_procedure_type = {}
    procedure_types_by_id = {}

    for mouse in mice:
        add_mouse_to_home_office_counts(total_counts, mouse)
        increment_crossing_type_counts(total_crossing_type_counts, mouse)
        protocol_counts = counts_by_protocol.get(mouse.protocol_id)

        if protocol_counts is not None:
            add_mouse_to_home_office_counts(protocol_counts, mouse)
            increment_crossing_type_counts(
                crossing_type_counts_by_protocol[mouse.protocol_id],
                mouse,
            )

        seen_procedure_type_ids = set()
        for procedure in mouse.procedures.all():
            procedure_type_id = procedure.procedure_type_id

            if procedure_type_id in seen_procedure_type_ids:
                continue

            seen_procedure_type_ids.add(procedure_type_id)
            procedure_types_by_id[procedure_type_id] = procedure.procedure_type
            procedure_counts = counts_by_procedure_type.setdefault(
                procedure_type_id,
                empty_home_office_counts(),
            )
            add_mouse_to_home_office_counts(procedure_counts, mouse)

    protocol_rows = []
    for protocol in protocols:
        counts = counts_by_protocol[protocol.pk]
        protocol_rows.append(
            {
                **counts,
                "protocol": protocol,
                "protocol_label": str(protocol),
                "max_severity": protocol.max_severity,
                "crossing_type_rows": crossing_type_rows(
                    crossing_type_counts_by_protocol[protocol.pk],
                ),
            }
        )

    procedure_rows = []
    for procedure_type_id, procedure_type in sorted(
        procedure_types_by_id.items(),
        key=lambda item: (item[1].name.lower(), item[0]),
    ):
        counts = counts_by_procedure_type[procedure_type_id]
        procedure_rows.append(
            {
                **counts,
                "procedure_type": procedure_type,
                "procedure_label": str(procedure_type),
            }
        )

    report = {
        "licence_reference": licence_reference,
        "start_date": start_date,
        "end_date": end_date,
        "output_mode": output_mode,
        "warning": HOME_OFFICE_WARNING,
        "total": total_counts,
        "crossing_type_labels": [
            *HOME_OFFICE_CROSSING_TYPE_LABELS,
            HOME_OFFICE_UNKNOWN_CROSSING_TYPE,
        ],
        "total_crossing_type_rows": crossing_type_rows(
            total_crossing_type_counts,
        ),
        "protocol_rows": protocol_rows,
        "procedure_rows": procedure_rows,
    }
    report["text"] = build_home_office_return_text(report)

    return report


def order_mice_page_queryset(queryset, sort, direction):
    if sort in {"date_of_birth", "cull_date"}:
        expression = (
            F(sort).desc(nulls_last=True)
            if direction == "desc"
            else F(sort).asc(nulls_last=True)
        )
        return queryset.order_by(expression, "mouse_id")

    if sort == "protocol":
        prefix = "-" if direction == "desc" else ""
        return queryset.order_by(
            f"{prefix}protocol__licence_reference",
            f"{prefix}protocol__protocol_number",
            f"{prefix}protocol_id",
            "mouse_id",
        )

    order_by_field = sort if direction == "asc" else f"-{sort}"
    return queryset.order_by(order_by_field, "mouse_id")


def order_projects_page_queryset(queryset, sort, direction):
    if sort in {"start_date", "end_date"}:
        expression = (
            F(sort).desc(nulls_last=True)
            if direction == "desc"
            else F(sort).asc(nulls_last=True)
        )
        return queryset.order_by(expression, "name", "project_id")

    order_by_field = sort if direction == "asc" else f"-{sort}"
    return queryset.order_by(order_by_field, "project_id")


def order_experiments_page_queryset(queryset, sort, direction):
    if sort in {"start_date", "end_date"}:
        expression = (
            F(sort).desc(nulls_last=True)
            if direction == "desc"
            else F(sort).asc(nulls_last=True)
        )
        return queryset.order_by(expression, "name", "experiment_id")

    if sort == "recording_type":
        prefix = "-" if direction == "desc" else ""
        return queryset.order_by(
            f"{prefix}recording_type__name",
            "name",
            "experiment_id",
        )

    order_by_field = sort if direction == "asc" else f"-{sort}"
    return queryset.order_by(order_by_field, "experiment_id")


def projects_page_url(status="active"):
    url = reverse("lab:projects_page")

    if status == "inactive":
        return f"{url}?{urlencode({'status': 'inactive'})}"

    return url


def experiments_page_url(project, status="current"):
    url = reverse("lab:experiments_page", args=[project.pk])

    if status == "archived":
        return f"{url}?{urlencode({'status': 'archived'})}"

    return url


def ensure_services_project():
    project = (
        Project.objects
        .filter(is_service=True, name=SERVICES_PROJECT_NAME)
        .order_by("pk")
        .first()
    )

    if project is None:
        project = (
            Project.objects
            .filter(name=SERVICES_PROJECT_NAME)
            .order_by("pk")
            .first()
        )

    if project is None:
        return Project.objects.create(
            name=SERVICES_PROJECT_NAME,
            description=SERVICES_PROJECT_DESCRIPTION,
            active=True,
            is_service=True,
        )

    updates = []

    if not project.is_service:
        project.is_service = True
        updates.append("is_service")

    if not project.active:
        project.active = True
        updates.append("active")

    if not project.description:
        project.description = SERVICES_PROJECT_DESCRIPTION
        updates.append("description")

    if updates:
        project.save(update_fields=updates)

    return project


def experiment_recordings_url(project, experiment):
    return reverse(
        "lab:recordings_page",
        args=[project.pk, experiment.pk],
    )


def add_recording_url(project, experiment):
    return reverse(
        "lab:add_recording",
        args=[project.pk, experiment.pk],
    )


def select_recording_mouse_url(project, experiment):
    return reverse(
        "lab:select_recording_mouse",
        args=[project.pk, experiment.pk],
    )


def recording_form_mouse_workflow_urls(
    project,
    experiment,
    form_url,
    selected_mouse_ids=None,
):
    return {
        "select_mouse_url": url_with_query_params(
            select_recording_mouse_url(project, experiment),
            return_url=form_url,
            selected_mouse=selected_mouse_ids or [],
        ),
    }


def service_recording_actions_for_mice(mice, return_url):
    mouse_ids = [
        str(mouse.pk)
        for mouse in mice
    ]

    if not mouse_ids:
        return []

    ensure_services_project()
    experiments = (
        Experiment.objects
        .select_related("project", "recording_type")
        .filter(
            project__is_service=True,
            archived=False,
            recording_type__isnull=False,
        )
        .order_by("name", "experiment_id")
    )
    actions = []

    for experiment in experiments:
        description_parts = [str(experiment.recording_type)]

        if experiment.description:
            description_parts.append(experiment.description)

        actions.append(
            {
                "title": experiment.name,
                "description": " | ".join(description_parts),
                "url": url_with_query_params(
                    add_recording_url(experiment.project, experiment),
                    return_url=return_url,
                    mice=recording_mice_query_value(mouse_ids),
                ),
            }
        )

    return actions


def selected_mice_from_request(request):
    selected_mouse_ids = selected_recording_mouse_ids_from_request(request)

    if not selected_mouse_ids:
        return []

    try:
        return selected_mice_for_ids(selected_mouse_ids)
    except Mouse.DoesNotExist as exc:
        raise Http404("One or more selected mice could not be found.") from exc


def recording_for_experiment_or_404(experiment, recording_pk):
    return get_object_or_404(
        experiment.recordings.select_related(
            "owner",
            "experiment",
            "recording_type",
        ).prefetch_related("mice"),
        pk=recording_pk,
    )


def require_recording_modify_permission(user, recording):
    if not recording.can_be_modified_by(user):
        raise PermissionDenied(
            "You do not have permission to modify this recording."
        )


def licence_references():
    return list(
        Protocol.objects
        .order_by("licence_reference")
        .values_list("licence_reference", flat=True)
        .distinct()
    )


def procedure_licence_url(licence_reference):
    return reverse(
        "lab:procedure_page_licence",
        kwargs={"licence_reference": licence_reference},
    )


def procedure_protocol_url(protocol):
    return reverse(
        "lab:procedure_page_licence_protocol",
        kwargs={
            "licence_reference": protocol.licence_reference,
            "protocol_number": protocol.protocol_number,
        },
    )


def edit_selected_mice_url(protocol):
    return reverse(
        "lab:edit_selected_mice_licence_protocol",
        kwargs={
            "licence_reference": protocol.licence_reference,
            "protocol_number": protocol.protocol_number,
        },
    )


def edit_selected_mice_all_url():
    return reverse("lab:edit_selected_mice_all")


def delete_selected_mice_url(protocol):
    return reverse(
        "lab:delete_selected_mice_licence_protocol",
        kwargs={
            "licence_reference": protocol.licence_reference,
            "protocol_number": protocol.protocol_number,
        },
    )


def delete_selected_mice_all_url():
    return reverse("lab:delete_selected_mice_all")


def redirect_preserving_query(request, url):
    query_string = request.GET.urlencode()

    if query_string:
        return redirect(f"{url}?{query_string}")

    return redirect(url)


def first_protocol_or_404(queryset):
    protocol = queryset.first()

    if protocol is None:
        raise Http404("No protocol matches the given query.")

    return protocol


def protocol_by_number_or_404(protocol_number, licence_reference=None):
    protocols = Protocol.objects.filter(
        protocol_number=protocol_number,
    ).order_by(
        "licence_reference",
        "protocol_number",
        "protocol_id",
    )

    if licence_reference is not None:
        protocols = protocols.filter(licence_reference=licence_reference)

    return first_protocol_or_404(protocols)


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

    sort = request.GET.get("sort", "mouse_id")
    direction = request.GET.get("direction", "asc")

    if direction not in {"asc", "desc"}:
        direction = "asc"

    if sort not in sortable_fields():
        sort = "mouse_id"

    order_by_field = sort if direction == "asc" else f"-{sort}"
    filter_values = read_filter_values(request.GET, MOUSE_FILTER_SPECS)
    procedure_types = ProcedureType.objects.all().order_by("name")
    current_year = timezone.localdate().year

    mice = (
        Mouse.objects
        .filter(protocol=selected_protocol)
        .prefetch_related("procedures__procedure_type")
    )

    mice = apply_filter_specs(mice, MOUSE_FILTER_SPECS, filter_values)

    if show_current_year_enabled(filter_values):
        mice = apply_current_year_filter(mice, current_year)

    if severity_exceeded_filter_enabled(filter_values):
        mice = apply_severity_exceeded_filter(mice)

    mice = mice.order_by(order_by_field)

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
        "record_count": build_record_count(page_obj, "mouse"),
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


def mice_page(request):
    sort = request.GET.get("sort", "date_of_birth")
    direction = request.GET.get("direction", "desc")

    if direction not in {"asc", "desc"}:
        direction = "desc"

    if sort not in mice_page_sortable_fields():
        sort = "date_of_birth"

    filter_specs = build_mice_page_filter_specs()
    filter_values = read_filter_values(request.GET, filter_specs)

    mice = Mouse.objects.select_related("protocol")
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
            "record_count": build_record_count(page_obj, "mouse"),
            "pagination": build_pagination(page_obj, query_params),
            "page_size_control": build_table_page_size_control(
                request.GET,
                page_size,
            ),
            "edit_selected_url": edit_selected_mice_all_url(),
            "add_mouse_url": reverse("lab:add_mouse_all"),
            "add_litter_url": reverse("lab:add_litter_all"),
        },
    )


def projects_page(request):
    status = request.GET.get("status", "active")

    if status not in {"active", "inactive"}:
        status = "active"

    sort = request.GET.get("sort", "name")
    direction = request.GET.get("direction", "asc")

    if direction not in {"asc", "desc"}:
        direction = "asc"

    if sort not in project_page_sortable_fields():
        sort = "name"

    filter_values = read_filter_values(request.GET, PROJECT_FILTER_SPECS)
    projects = Project.objects.filter(
        active=(status == "active"),
        is_service=False,
    )
    projects = apply_filter_specs(projects, PROJECT_FILTER_SPECS, filter_values)
    projects = order_projects_page_queryset(projects, sort, direction)

    paginator = Paginator(projects, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)

    return render(
        request,
        "lab/projects_page.html",
        {
            "status": status,
            "project_tabs": [
                {
                    "label": "Active",
                    "url": projects_page_url("active"),
                    "active": status == "active",
                },
                {
                    "label": "Inactive",
                    "url": projects_page_url("inactive"),
                    "active": status == "inactive",
                },
            ],
            "table": build_projects_table(
                page_obj,
                sort,
                direction,
                query_params,
                return_url=request.get_full_path(),
            ),
            "filter_form": build_filter_form(
                PROJECT_FILTER_SPECS,
                filter_values,
                hidden_fields=[
                    {"name": "status", "value": status},
                    {"name": "sort", "value": sort},
                    {"name": "direction", "value": direction},
                ],
                clear_url=projects_page_url(status),
            ),
            "record_count": build_record_count(page_obj, "project"),
            "pagination": build_pagination(page_obj, query_params),
        },
    )


def add_project(request):
    return_url = safe_return_url(request, reverse("lab:projects_page"))

    if request.method == "POST":
        form = ProjectForm(request.POST)

        if form.is_valid():
            project = form.save()
            messages.success(
                request,
                f"Project {project.name} was created successfully.",
            )
            return redirect(return_url)
    else:
        form = ProjectForm()

    return render(
        request,
        "lab/add_project.html",
        {
            "form": form,
            "return_url": return_url,
        },
    )


def edit_project(request, project_pk):
    project = get_object_or_404(Project, pk=project_pk, is_service=False)
    return_url = safe_return_url(request, reverse("lab:projects_page"))

    if request.method == "POST":
        form = ProjectForm(request.POST, instance=project)

        if form.is_valid():
            project = form.save()
            messages.success(
                request,
                f"Project {project.name} was updated successfully.",
            )
            return redirect(return_url)
    else:
        form = ProjectForm(instance=project)

    return render(
        request,
        "lab/edit_project.html",
        {
            "form": form,
            "project": project,
            "return_url": return_url,
        },
    )


def experiments_page(request, project_pk):
    project = get_object_or_404(Project, pk=project_pk)

    if project.is_service:
        return redirect("lab:services_page")

    status = request.GET.get("status", "current")

    if status not in {"current", "archived"}:
        status = "current"

    sort = request.GET.get("sort", "name")
    direction = request.GET.get("direction", "asc")

    if direction not in {"asc", "desc"}:
        direction = "asc"

    if sort not in experiment_page_sortable_fields():
        sort = "name"

    filter_values = read_filter_values(request.GET, EXPERIMENT_FILTER_SPECS)
    experiments = project.experiments.select_related("recording_type").filter(
        archived=(status == "archived"),
    )
    experiments = apply_filter_specs(
        experiments,
        EXPERIMENT_FILTER_SPECS,
        filter_values,
    )
    experiments = order_experiments_page_queryset(experiments, sort, direction)

    paginator = Paginator(experiments, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)

    return render(
        request,
        "lab/experiments_page.html",
        {
            "project": project,
            "status": status,
            "experiment_tabs": [
                {
                    "label": "Current",
                    "url": experiments_page_url(project, "current"),
                    "active": status == "current",
                },
                {
                    "label": "Archived",
                    "url": experiments_page_url(project, "archived"),
                    "active": status == "archived",
                },
            ],
            "table": build_experiments_table(
                page_obj,
                sort,
                direction,
                query_params,
                return_url=request.get_full_path(),
            ),
            "filter_form": build_filter_form(
                EXPERIMENT_FILTER_SPECS,
                filter_values,
                hidden_fields=[
                    {"name": "status", "value": status},
                    {"name": "sort", "value": sort},
                    {"name": "direction", "value": direction},
                ],
                clear_url=experiments_page_url(project, status),
            ),
            "record_count": build_record_count(page_obj, "experiment"),
            "pagination": build_pagination(page_obj, query_params),
        },
    )


def services_page(request):
    ensure_services_project()

    experiments = (
        Experiment.objects
        .select_related("project", "recording_type")
        .filter(
            project__is_service=True,
            archived=False,
        )
        .order_by("name", "experiment_id")
    )

    return render(
        request,
        "lab/services_page.html",
        {
            "cards": build_service_experiment_cards(experiments),
        },
    )


def lines_crossings_url(species=None):
    url = reverse("lab:lines_crossings_page")

    if species is None:
        return url

    return f"{url}?{urlencode({'species': species.pk})}"


def ensure_default_species():
    species, _ = Species.objects.get_or_create(
        name=DEFAULT_SPECIES_NAME,
        defaults={"active": True},
    )
    return species


def selected_species_from_request(request):
    species_id = request.POST.get("species") or request.GET.get("species")

    if species_id:
        species = Species.objects.filter(pk=species_id).first()

        if species is not None:
            return species

    return (
        Species.objects.filter(name=DEFAULT_SPECIES_NAME).first()
        or Species.objects.order_by("name").first()
        or ensure_default_species()
    )


def count_label(count, singular, plural=None):
    if count == 1:
        return f"1 {singular}"

    return f"{count} {plural or singular + 's'}"


def delete_line_from_request(request):
    line = get_object_or_404(
        MouseLine.objects.select_related("species"),
        pk=request.POST.get("line_id"),
    )
    species = line.species
    crossings = list(
        line.crossings
        .prefetch_related("lines")
        .order_by("display_name", "crossing_id")
    )
    deletable_single_crossings = []
    blocking_crossings = []

    for crossing in crossings:
        crossing_lines = list(crossing.lines.all())

        if (
            len(crossing_lines) == 1
            and crossing_lines[0].pk == line.pk
            and not crossing.mice.exists()
        ):
            deletable_single_crossings.append(crossing)
        else:
            blocking_crossings.append(crossing)

    genotype_mouse_count = (
        Mouse.objects
        .filter(genotype_calls__mouse_line=line)
        .distinct()
        .count()
    )
    linked_reasons = []

    if blocking_crossings:
        linked_reasons.append(
            count_label(len(blocking_crossings), "crossing")
        )

    if genotype_mouse_count:
        linked_reasons.append(
            count_label(genotype_mouse_count, "mouse genotype record")
        )

    if linked_reasons:
        messages.error(
            request,
            (
                f"Line {line.name} cannot be deleted because it is linked to "
                f"{' and '.join(linked_reasons)}."
            ),
        )
        return redirect(lines_crossings_url(species))

    try:
        for crossing in deletable_single_crossings:
            crossing.delete()

        line.delete()
    except ProtectedError:
        messages.error(
            request,
            f"Line {line.name} could not be deleted because it is still linked.",
        )
    else:
        messages.success(
            request,
            f"Line {line.name} was deleted.",
        )

    return redirect(lines_crossings_url(species))


def delete_crossing_from_request(request):
    crossing = get_object_or_404(
        Crossing.objects.select_related("species"),
        pk=request.POST.get("crossing_id"),
    )
    species = crossing.species
    linked_mouse_count = crossing.mice.count()

    if linked_mouse_count:
        linked_mouse_label = count_label(linked_mouse_count, "mouse")
        linked_mouse_verb = "uses" if linked_mouse_count == 1 else "use"
        messages.error(
            request,
            (
                f"Crossing {crossing.display_name} cannot be deleted because "
                f"{linked_mouse_label} {linked_mouse_verb} it."
            ),
        )
        return redirect(lines_crossings_url(species))

    try:
        crossing.delete()
    except ProtectedError:
        messages.error(
            request,
            (
                f"Crossing {crossing.display_name} could not be deleted "
                "because it is still linked."
            ),
        )
    else:
        messages.success(
            request,
            f"Crossing {crossing.display_name} was deleted.",
        )

    return redirect(lines_crossings_url(species))


def lines_crossings_page(request):
    if not request.user.is_staff:
        raise PermissionDenied

    selected_species = selected_species_from_request(request)
    line_form = BulkMouseLineCreateForm(initial={"species": selected_species})
    crossing_form = CrossingCreateForm(initial={"species": selected_species})

    if request.method == "POST":
        if request.POST.get("action") == "add_lines":
            line_form = BulkMouseLineCreateForm(request.POST)

            if line_form.is_valid():
                created_lines, existing_lines = line_form.save()
                selected_species = line_form.cleaned_data["species"]
                messages.success(
                    request,
                    (
                        f"Created {len(created_lines)} line"
                        f"{'' if len(created_lines) == 1 else 's'}; "
                        f"skipped {len(existing_lines)} duplicate"
                        f"{'' if len(existing_lines) == 1 else 's'}."
                    ),
                )
                return redirect(lines_crossings_url(selected_species))
        elif request.POST.get("action") == "create_crossing":
            crossing_form = CrossingCreateForm(request.POST)

            if crossing_form.is_valid():
                crossing, created = crossing_form.save()
                selected_species = crossing.species

                if created:
                    messages.success(
                        request,
                        f"Created crossing {crossing.display_name}.",
                    )
                else:
                    messages.info(
                        request,
                        f"Crossing {crossing.display_name} already exists.",
                    )

                return redirect(lines_crossings_url(selected_species))
        elif request.POST.get("action") == "delete_line":
            return delete_line_from_request(request)
        elif request.POST.get("action") == "delete_crossing":
            return delete_crossing_from_request(request)

    species_options = Species.objects.order_by("name")
    lines = (
        MouseLine.objects
        .filter(species=selected_species)
        .order_by("name", "mouse_line_id")
    )
    crossings = (
        Crossing.objects
        .filter(species=selected_species)
        .prefetch_related("lines")
        .order_by("display_name", "crossing_id")
    )

    return render(
        request,
        "lab/lines_crossings_page.html",
        {
            "selected_species": selected_species,
            "species_options": species_options,
            "line_form": line_form,
            "crossing_form": crossing_form,
            "lines": lines,
            "crossings": crossings,
            "line_lookup_url": reverse("lab:line_lookup"),
        },
    )


def line_lookup(request):
    if not request.user.is_staff:
        raise PermissionDenied

    species_id = request.GET.get("species")
    query = (request.GET.get("q") or "").strip()
    lines = MouseLine.objects.filter(active=True)

    if species_id:
        lines = lines.filter(species_id=species_id)

    if query:
        lines = lines.filter(name__icontains=query)

    lines = lines.select_related("species").order_by("name", "mouse_line_id")[:20]

    return JsonResponse(
        {
            "results": [
                {
                    "id": line.pk,
                    "name": line.name,
                    "label": f"{line.name} ({line.species.name})",
                    "species_id": line.species_id,
                }
                for line in lines
            ]
        }
    )


def crossing_lookup(request):
    query = (request.GET.get("q") or "").strip()
    crossings = Crossing.objects.filter(active=True).select_related("species")

    if query:
        crossings = crossings.filter(
            Q(display_name__icontains=query)
            | Q(species__name__icontains=query)
        )

    crossings = crossings.order_by("species__name", "display_name", "crossing_id")[:20]

    return JsonResponse(
        {
            "results": [
                {
                    "id": crossing.pk,
                    "label": crossing_choice_label(crossing),
                    "display_name": crossing.display_name,
                    "species": crossing.species.name,
                }
                for crossing in crossings
            ]
        }
    )


def crossing_genotype_fields(request):
    crossing = get_object_or_404(
        Crossing.objects.prefetch_related("lines"),
        pk=request.GET.get("crossing"),
        active=True,
    )
    mouse_id = request.GET.get("mouse")
    existing_calls = {}

    if mouse_id:
        mouse = Mouse.objects.filter(pk=mouse_id).first()

        if mouse is not None:
            existing_calls = {
                genotype.mouse_line_id: genotype.zygosity
                for genotype in mouse.genotype_calls.all()
            }

    lines = crossing.lines.order_by("name", "mouse_line_id")

    return JsonResponse(
        {
            "crossing": {
                "id": crossing.pk,
                "label": crossing_choice_label(crossing),
            },
            "choices": [
                {"value": value, "label": label}
                for value, label in ZYGOSITY_CHOICES
            ],
            "lines": [
                {
                    "id": line.pk,
                    "name": line.name,
                    "field_name": genotype_field_name(line),
                    "value": existing_calls.get(line.pk, "unknown"),
                }
                for line in lines
            ],
        }
    )


def add_experiment(request, project_pk):
    project = get_object_or_404(Project, pk=project_pk, is_service=False)
    return_url = safe_return_url(
        request,
        experiments_page_url(project),
    )

    if request.method == "POST":
        form = ExperimentForm(request.POST)

        if form.is_valid():
            experiment = form.save(commit=False)
            experiment.project = project
            experiment.save()
            messages.success(
                request,
                f"Experiment {experiment.name} was created successfully.",
            )
            return redirect(return_url)
    else:
        form = ExperimentForm()

    return render(
        request,
        "lab/add_experiment.html",
        {
            "form": form,
            "project": project,
            "return_url": return_url,
        },
    )


def edit_experiment(request, project_pk, experiment_pk):
    project = get_object_or_404(Project, pk=project_pk, is_service=False)
    experiment = get_object_or_404(
        Experiment,
        pk=experiment_pk,
        project=project,
    )
    return_url = safe_return_url(
        request,
        experiments_page_url(project),
    )

    if request.method == "POST":
        form = ExperimentForm(request.POST, instance=experiment)

        if form.is_valid():
            experiment = form.save()
            messages.success(
                request,
                f"Experiment {experiment.name} was updated successfully.",
            )
            return redirect(return_url)
    else:
        form = ExperimentForm(instance=experiment)

    return render(
        request,
        "lab/edit_experiment.html",
        {
            "form": form,
            "project": project,
            "experiment": experiment,
            "return_url": return_url,
        },
    )


def experiment_for_project_or_404(project_pk, experiment_pk):
    project = get_object_or_404(Project, pk=project_pk)
    experiment = get_object_or_404(
        Experiment.objects.select_related("recording_type", "project"),
        pk=experiment_pk,
        project=project,
    )
    return project, experiment


def positive_int_or_none(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None

    return parsed if parsed > 0 else None


def selected_recording_page_number(recordings, selected_recording_id, per_page):
    selected = (
        recordings
        .filter(pk=selected_recording_id)
        .values("recording_date", "recording_id")
        .first()
    )

    if selected is None:
        return None

    earlier_rows = recordings.filter(
        Q(recording_date__gt=selected["recording_date"])
        | Q(
            recording_date=selected["recording_date"],
            recording_id__gt=selected["recording_id"],
        )
    ).count()

    return earlier_rows // per_page + 1


def recording_field_filter_name(recording_field):
    return f"field_{recording_field.pk}"


def recording_field_filter_label(recording_field):
    if recording_field.units:
        return f"{recording_field.label} ({recording_field.units})"

    return recording_field.label


def recording_field_filter_specs(recording_type):
    if recording_type is None:
        return []

    filter_specs = []
    recording_fields = (
        recording_type.fields
        .filter(active=True, filterable=True)
        .order_by("order", "label")
    )

    for recording_field in recording_fields:
        spec = {
            "name": recording_field_filter_name(recording_field),
            "label": recording_field_filter_label(recording_field),
            "recording_field": recording_field,
        }

        if recording_field.data_type == RecordingField.CHOICE:
            spec.update(
                {
                    "type": "select",
                    "choices": [
                        ("", "All"),
                        *normalize_choice_values(recording_field.choices),
                    ],
                }
            )
        elif recording_field.data_type == RecordingField.BOOLEAN:
            spec.update(
                {
                    "type": "select",
                    "choices": [
                        ("", "All"),
                        ("true", "Yes"),
                        ("false", "No"),
                    ],
                }
            )
        elif recording_field.data_type == RecordingField.DATE:
            spec["type"] = "date"
        else:
            spec.update(
                {
                    "type": "text",
                    "placeholder": f"Type {recording_field.label.lower()}",
                }
            )

            if recording_field.key == "crossing":
                spec["autocomplete"] = CROSSING_FILTER_AUTOCOMPLETE

        filter_specs.append(spec)

    return filter_specs


def parse_recording_field_filter_value(recording_field, value):
    if recording_field.data_type == RecordingField.BOOLEAN:
        return value == "true", True

    if recording_field.data_type == RecordingField.INTEGER:
        try:
            return int(value), True
        except ValueError:
            return None, False

    if recording_field.data_type == RecordingField.FLOAT:
        try:
            return float(value), True
        except ValueError:
            return None, False

    return value, True


def apply_recording_field_filter_specs(queryset, filter_specs, values):
    for spec in filter_specs:
        recording_field = spec["recording_field"]
        value = values.get(spec["name"])

        if not value:
            continue

        lookup_root = f"values__{recording_field.key}"

        if recording_field.data_type == RecordingField.TEXT:
            queryset = queryset.filter(**{f"{lookup_root}__icontains": value})
            continue

        typed_value, is_valid = parse_recording_field_filter_value(
            recording_field,
            value,
        )

        if not is_valid:
            return queryset.none()

        queryset = queryset.filter(**{lookup_root: typed_value})

    return queryset


def recordings_page(request, project_pk, experiment_pk):
    project, experiment = experiment_for_project_or_404(
        project_pk,
        experiment_pk,
    )
    dynamic_filter_specs = recording_field_filter_specs(experiment.recording_type)
    filter_specs = [*RECORDING_FILTER_SPECS, *dynamic_filter_specs]
    filter_values = read_filter_values(request.GET, filter_specs)
    recordings = (
        experiment.recordings
        .select_related("owner", "experiment", "recording_type")
        .prefetch_related("mice")
        .all()
    )
    recordings = apply_filter_specs(
        recordings,
        RECORDING_FILTER_SPECS,
        filter_values,
    )
    recordings = apply_recording_field_filter_specs(
        recordings,
        dynamic_filter_specs,
        filter_values,
    )

    if filter_values.get("date_from"):
        recordings = recordings.filter(
            recording_date__gte=filter_values["date_from"],
        )

    if filter_values.get("date_to"):
        recordings = recordings.filter(
            recording_date__lte=filter_values["date_to"],
        )

    recordings = recordings.distinct().order_by(
        "-recording_date",
        "-recording_id",
    )

    page_size = read_table_page_size(request.GET)
    paginator = Paginator(recordings, page_size)
    selected_recording_id = positive_int_or_none(
        request.GET.get("selected_recording")
    )
    page_number = request.GET.get("page")

    if selected_recording_id and not page_number:
        page_number = selected_recording_page_number(
            recordings,
            selected_recording_id,
            paginator.per_page,
        )

    page_obj = paginator.get_page(page_number)
    query_params = request.GET.copy()
    query_params.pop("page", None)
    recordings_url = experiment_recordings_url(project, experiment)
    experiments_url = (
        reverse("lab:services_page")
        if project.is_service
        else experiments_page_url(project)
    )
    experiments_link_label = "Services" if project.is_service else "Experiment Page"

    return render(
        request,
        "lab/recordings_page.html",
        {
            "project": project,
            "experiment": experiment,
            "table": build_recordings_table(
                page_obj,
                experiment.recording_type,
                request.user,
                selected_recording_id=selected_recording_id,
            ),
            "filter_form": build_filter_form(
                filter_specs,
                filter_values,
                hidden_fields=[
                    {"name": "per_page", "value": page_size},
                ],
                clear_url=recordings_url,
            ),
            "record_count": build_record_count(page_obj, "recording"),
            "pagination": build_pagination(page_obj, query_params),
            "page_size_control": build_table_page_size_control(
                request.GET,
                page_size,
            ),
            "recordings_url": recordings_url,
            "add_recording_url": reverse(
                "lab:add_recording",
                args=[project.pk, experiment.pk],
            ),
            "duplicate_recording_url": reverse(
                "lab:duplicate_recording",
                args=[project.pk, experiment.pk],
            ),
            "experiments_url": experiments_url,
            "experiments_link_label": experiments_link_label,
        },
    )


def select_recording_mouse(request, project_pk, experiment_pk):
    project, experiment = experiment_for_project_or_404(
        project_pk,
        experiment_pk,
    )
    fallback_url = add_recording_url(project, experiment)
    return_url = safe_return_url(request, fallback_url)
    selected_mouse_id_values = selected_mouse_ids(request)

    if request.method == "POST":
        return redirect(
            url_with_query_params(
                return_url,
                mice=recording_mice_query_value(selected_mouse_id_values),
                mouse=None,
            )
        )

    sort = request.GET.get("sort", "date_of_birth")
    direction = request.GET.get("direction", "desc")

    if direction not in {"asc", "desc"}:
        direction = "desc"

    if sort not in mice_page_sortable_fields():
        sort = "date_of_birth"

    filter_specs = build_mice_page_filter_specs()
    filter_values = read_filter_values(request.GET, filter_specs)
    mice = Mouse.objects.select_related("protocol")
    mice = apply_filter_specs(mice, filter_specs, filter_values)
    mice = order_mice_page_queryset(mice, sort, direction)

    page_size = read_table_page_size(request.GET)
    paginator = Paginator(mice, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    selector_url = select_recording_mouse_url(project, experiment)
    visible_mouse_ids = {
        mouse.pk
        for mouse in page_obj
    }
    hidden_selected_mouse_ids = [
        mouse_id
        for mouse_id in selected_mouse_id_values
        if mouse_id not in visible_mouse_ids
    ]
    selected_mice = []

    if selected_mouse_id_values:
        try:
            selected_mice = selected_mice_for_ids(selected_mouse_id_values)
        except Mouse.DoesNotExist:
            selected_mice = []

    selected_return_url = url_with_query_params(
        return_url,
        mice=recording_mice_query_value(selected_mouse_id_values),
        mouse=None,
    )

    return render(
        request,
        "lab/select_recording_mouse.html",
        {
            "project": project,
            "experiment": experiment,
            "table": build_mouse_selector_table(
                page_obj,
                sort,
                direction,
                query_params,
                selected_mouse_id_values,
            ),
            "filter_form": build_filter_form(
                filter_specs,
                filter_values,
                hidden_fields=[
                    {"name": "return_url", "value": return_url},
                    *[
                        {"name": "selected_mouse", "value": mouse_id}
                        for mouse_id in selected_mouse_id_values
                    ],
                    {"name": "sort", "value": sort},
                    {"name": "direction", "value": direction},
                    {"name": "per_page", "value": page_size},
                ],
                clear_url=url_with_query_params(
                    selector_url,
                    return_url=return_url,
                ),
            ),
            "record_count": build_record_count(page_obj, "mouse"),
            "pagination": build_pagination(page_obj, query_params),
            "page_size_control": build_table_page_size_control(
                request.GET,
                page_size,
            ),
            "return_url": return_url,
            "selected_mice": selected_mice,
            "hidden_selected_mouse_ids": hidden_selected_mouse_ids,
            "add_mouse_url": url_with_query_params(
                reverse("lab:add_mouse_all"),
                return_url=selected_return_url,
                return_with_mouse="1",
            ),
        },
    )


def add_recording(request, project_pk, experiment_pk):
    project, experiment = experiment_for_project_or_404(
        project_pk,
        experiment_pk,
    )
    recordings_url = experiment_recordings_url(project, experiment)
    return_url = safe_return_url(request, recordings_url)
    form_url = (
        request.get_full_path()
        if request.method == "GET"
        else url_with_query_params(
            add_recording_url(project, experiment),
            return_url=return_url,
        )
    )
    selected_mice = (
        selected_mice_from_request(request)
        if request.method == "GET"
        else []
    )

    if experiment.recording_type_id is None:
        messages.error(
            request,
            "Select an experiment type before adding recordings.",
        )
        return redirect(recordings_url)

    source_recording = None
    duplicate_id = request.GET.get("duplicate")

    if duplicate_id:
        source_recording = recording_for_experiment_or_404(
            experiment,
            duplicate_id,
        )
        if source_recording.recording_type_id != experiment.recording_type_id:
            raise Http404("Recording does not belong to this experiment type.")
        require_recording_modify_permission(request.user, source_recording)

    if request.method == "POST":
        form = RecordingForm(
            request.POST,
            experiment=experiment,
            owner=request.user,
        )

        if form.is_valid():
            recording = form.save()
            messages.success(
                request,
                f"Recording {recording.pk} was created successfully.",
            )
            return redirect(return_url)
    else:
        form = RecordingForm(
            experiment=experiment,
            source_recording=source_recording,
            owner=request.user,
            selected_mice=selected_mice,
        )

    mouse_workflow_urls = recording_form_mouse_workflow_urls(
        project,
        experiment,
        form_url,
        form.selected_mouse_ids(),
    )

    return render(
        request,
        "lab/add_recording.html",
        {
            "form": form,
            "project": project,
            "experiment": experiment,
            "source_recording": source_recording,
            "return_url": return_url,
            "selected_mice": form.selected_mice_for_display(),
            **mouse_workflow_urls,
        },
    )


@require_POST
def duplicate_recording(request, project_pk, experiment_pk):
    project, experiment = experiment_for_project_or_404(
        project_pk,
        experiment_pk,
    )
    return_url = safe_return_url(
        request,
        experiment_recordings_url(project, experiment),
    )
    selected_recordings = request.POST.getlist("selected_recording")

    if len(selected_recordings) != 1:
        messages.error(
            request,
            "Select exactly one recording to duplicate.",
        )
        return redirect(return_url)

    recording = get_object_or_404(
        experiment.recordings,
        pk=selected_recordings[0],
    )
    require_recording_modify_permission(request.user, recording)
    add_url = reverse(
        "lab:add_recording",
        args=[project.pk, experiment.pk],
    )
    query = urlencode(
        {
            "duplicate": recording.pk,
            "return_url": return_url,
        }
    )

    return redirect(f"{add_url}?{query}")


def edit_recording(request, project_pk, experiment_pk, recording_pk):
    project, experiment = experiment_for_project_or_404(
        project_pk,
        experiment_pk,
    )
    recording = recording_for_experiment_or_404(experiment, recording_pk)
    require_recording_modify_permission(request.user, recording)
    recordings_url = experiment_recordings_url(project, experiment)
    return_url = safe_return_url(request, recordings_url)
    edit_url = reverse(
        "lab:edit_recording",
        args=[project.pk, experiment.pk, recording.pk],
    )
    form_url = (
        request.get_full_path()
        if request.method == "GET"
        else url_with_query_params(edit_url, return_url=return_url)
    )
    selected_mice = (
        selected_mice_from_request(request)
        if request.method == "GET"
        else []
    )

    if request.method == "POST":
        form = RecordingForm(
            request.POST,
            experiment=experiment,
            instance=recording,
        )

        if form.is_valid():
            recording = form.save()
            messages.success(
                request,
                f"Recording {recording.pk} was updated successfully.",
            )
            return redirect(return_url)
    else:
        form = RecordingForm(
            experiment=experiment,
            instance=recording,
            selected_mice=selected_mice,
        )

    mouse_workflow_urls = recording_form_mouse_workflow_urls(
        project,
        experiment,
        form_url,
        form.selected_mouse_ids(),
    )

    return render(
        request,
        "lab/edit_recording.html",
        {
            "form": form,
            "project": project,
            "experiment": experiment,
            "recording": recording,
            "return_url": return_url,
            "selected_mice": form.selected_mice_for_display(),
            **mouse_workflow_urls,
        },
    )


def delete_recording(request, project_pk, experiment_pk, recording_pk):
    project, experiment = experiment_for_project_or_404(
        project_pk,
        experiment_pk,
    )
    recording = recording_for_experiment_or_404(experiment, recording_pk)
    require_recording_modify_permission(request.user, recording)
    recordings_url = experiment_recordings_url(project, experiment)
    return_url = safe_return_url(request, recordings_url)

    if request.method == "POST":
        recording_pk = recording.pk
        recording.delete()
        messages.success(
            request,
            f"Recording {recording_pk} was deleted successfully.",
        )
        return redirect(return_url)

    return render(
        request,
        "lab/delete_recording.html",
        {
            "project": project,
            "experiment": experiment,
            "recording": recording,
            "return_url": return_url,
        },
    )


def edit_mouse(request, mouse_pk):
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
    edit_url = url_with_query_params(
        reverse("lab:edit_mouse", args=[mouse.pk]),
        return_url=return_url,
    )
    procedures = (
        mouse.procedures
        .select_related("procedure_type")
        .order_by("-date", "-procedure_id", "procedure_type__name")
    )

    if request.method == "POST":
        if "save_procedure" in request.POST:
            mouse_form = MouseEditForm(instance=mouse)
            procedure_form = ProcedureCreateForm(request.POST, mouse=mouse)

            if procedure_form.is_valid():
                with transaction.atomic():
                    create_mouse_procedure(
                        mouse,
                        procedure_form,
                    )

                messages.success(
                    request,
                    (
                        f"Procedure {procedure_form.cleaned_data['procedure_type']} "
                        f"was added for mouse {mouse.mouse_id}."
                    ),
                )
                edit_url = reverse("lab:edit_mouse", args=[mouse.pk])
                return redirect(
                    f"{edit_url}?{urlencode({'return_url': return_url})}"
                )
        else:
            mouse_form = MouseEditForm(request.POST, instance=mouse)
            procedure_form = ProcedureCreateForm(mouse=mouse)

            if mouse_form.is_valid():
                with transaction.atomic():
                    mouse = mouse_form.save()
                    mouse_form.save_structured_genetics(mouse)

                messages.success(
                    request,
                    f"Mouse {mouse.mouse_id} was updated successfully.",
                )
                return redirect(return_url)
    else:
        mouse_form = MouseEditForm(instance=mouse)
        procedure_form = ProcedureCreateForm(mouse=mouse)

    return render(
        request,
        "lab/edit_mouse.html",
        {
            "mouse_form": mouse_form,
            "procedure_form": procedure_form,
            "procedures": procedures,
            "mouse": mouse,
            "return_url": return_url,
            "service_recording_actions": service_recording_actions_for_mice(
                [mouse],
                edit_url,
            ),
            "crossing_lookup_url": reverse("lab:crossing_lookup"),
            "crossing_genotype_fields_url": reverse(
                "lab:crossing_genotype_fields"
            ),
            "crossing_mouse_id": mouse.pk,
        },
    )


def rename_mouse(request, mouse_pk):
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

    if request.method == "POST":
        form = MouseRenameForm(request.POST, mouse=mouse)

        if form.is_valid():
            old_mouse_id = mouse.pk

            try:
                with transaction.atomic():
                    renamed_mouse = rename_mouse_identity(
                        old_mouse_id,
                        form.cleaned_data["new_mouse_id"],
                    )
            except IntegrityError:
                form.add_error(
                    "new_mouse_id",
                    "A mouse with this ID already exists.",
                )
            else:
                messages.success(
                    request,
                    (
                        f"Mouse {old_mouse_id} was renamed to "
                        f"{renamed_mouse.mouse_id}."
                    ),
                )
                edit_url = reverse(
                    "lab:edit_mouse",
                    args=[renamed_mouse.pk],
                )
                return redirect(
                    f"{edit_url}?{urlencode({'return_url': return_url})}"
                )
    else:
        form = MouseRenameForm(mouse=mouse)

    return render(
        request,
        "lab/rename_mouse.html",
        {
            "form": form,
            "mouse": mouse,
            "return_url": return_url,
        },
    )


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
                    locked_mouse = Mouse.objects.select_for_update().get(
                        pk=mouse_id,
                    )
                    locked_mouse.delete()
            except Mouse.DoesNotExist:
                messages.error(
                    request,
                    "Mouse could not be found.",
                )
            except ProtectedError:
                messages.error(
                    request,
                    (
                        "Mouse could not be deleted because another record "
                        "still protects it."
                    ),
                )
            else:
                messages.success(
                    request,
                    f"Mouse {mouse_id} was deleted.",
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
                f"Procedure was updated for mouse {mouse.mouse_id}.",
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
        f"Procedure {procedure_name} was deleted for mouse {mouse.mouse_id}.",
    )
    edit_url = reverse("lab:edit_mouse", args=[mouse.pk])
    return redirect(
        f"{edit_url}?{urlencode({'return_url': return_url})}"
    )


def edit_selected_mice(request, protocol_number, licence_reference=None):
    selected_protocol = protocol_by_number_or_404(
        protocol_number=protocol_number,
        licence_reference=licence_reference,
    )
    fallback_url = procedure_protocol_url(selected_protocol)
    return_url = safe_return_url(request, fallback_url)
    mouse_ids = selected_mouse_ids(request)

    if not mouse_ids:
        messages.error(
            request,
            "Select at least one mouse before editing.",
        )
        return redirect(return_url)

    try:
        selected_mice = selected_mice_for_protocol(
            mouse_ids,
            selected_protocol,
        )
    except Mouse.DoesNotExist:
        messages.error(
            request,
            "One or more selected mice could not be found for this protocol.",
        )
        return redirect(return_url)

    can_add_bulk_procedure = all(
        mouse.protocol.allows_regulated_procedures
        for mouse in selected_mice
    )

    if request.method == "POST" and "apply_bulk_edit" not in request.POST:
        if len(selected_mice) == 1:
            edit_url = reverse(
                "lab:edit_mouse",
                args=[selected_mice[0].pk],
            )
            return redirect(
                f"{edit_url}?{urlencode({'return_url': return_url})}"
            )

        query_params = [
            ("return_url", return_url),
            *[
                ("selected_mouse", mouse_id)
                for mouse_id in mouse_ids
            ],
        ]
        bulk_url = edit_selected_mice_url(selected_protocol)
        return redirect(f"{bulk_url}?{urlencode(query_params)}")

    if request.method == "POST":
        form = BulkMouseUpdateForm(request.POST, selected_mice=selected_mice)

        if form.is_valid():
            with transaction.atomic():
                apply_bulk_mouse_updates(
                    selected_mice,
                    form,
                )

            messages.success(
                request,
                (
                    f"{len(selected_mice)} mice were updated successfully."
                ),
            )
            return redirect(return_url)
    else:
        form = BulkMouseUpdateForm(selected_mice=selected_mice)

    bulk_return_url = url_with_query_params(
        edit_selected_mice_url(selected_protocol),
        return_url=return_url,
        selected_mouse=mouse_ids,
    )
    return render(
        request,
        "lab/edit_selected_mice.html",
        {
            "form": form,
            "selected_mice": selected_mice,
            "selected_protocol": selected_protocol,
            "delete_selected_url": delete_selected_mice_url(selected_protocol),
            "return_url": return_url,
            "can_add_bulk_procedure": can_add_bulk_procedure,
            "service_recording_actions": service_recording_actions_for_mice(
                selected_mice,
                bulk_return_url,
            ),
            "crossing_lookup_url": reverse("lab:crossing_lookup"),
            "crossing_genotype_fields_url": reverse(
                "lab:crossing_genotype_fields"
            ),
        },
    )


def edit_selected_mice_all(request):
    fallback_url = reverse("lab:mice_page")
    return_url = safe_return_url(request, fallback_url)
    mouse_ids = selected_mouse_ids(request)

    if not mouse_ids:
        messages.error(
            request,
            "Select at least one mouse before editing.",
        )
        return redirect(return_url)

    try:
        selected_mice = selected_mice_for_ids(mouse_ids)
    except Mouse.DoesNotExist:
        messages.error(
            request,
            "One or more selected mice could not be found.",
        )
        return redirect(return_url)

    can_add_bulk_procedure = all(
        mouse.protocol.allows_regulated_procedures
        for mouse in selected_mice
    )

    if request.method == "POST" and "apply_bulk_edit" not in request.POST:
        if len(selected_mice) == 1:
            edit_url = reverse(
                "lab:edit_mouse",
                args=[selected_mice[0].pk],
            )
            return redirect(
                f"{edit_url}?{urlencode({'return_url': return_url})}"
            )

        query_params = [
            ("return_url", return_url),
            *[
                ("selected_mouse", mouse_id)
                for mouse_id in mouse_ids
            ],
        ]
        bulk_url = edit_selected_mice_all_url()
        return redirect(f"{bulk_url}?{urlencode(query_params)}")

    if request.method == "POST":
        form = BulkMouseUpdateForm(request.POST, selected_mice=selected_mice)

        if form.is_valid():
            with transaction.atomic():
                apply_bulk_mouse_updates(
                    selected_mice,
                    form,
                )

            messages.success(
                request,
                (
                    f"{len(selected_mice)} mice were updated successfully."
                ),
            )
            return redirect(return_url)
    else:
        form = BulkMouseUpdateForm(selected_mice=selected_mice)

    bulk_return_url = url_with_query_params(
        edit_selected_mice_all_url(),
        return_url=return_url,
        selected_mouse=mouse_ids,
    )
    return render(
        request,
        "lab/edit_selected_mice.html",
        {
            "form": form,
            "selected_mice": selected_mice,
            "selected_protocol": None,
            "delete_selected_url": delete_selected_mice_all_url(),
            "return_url": return_url,
            "can_add_bulk_procedure": can_add_bulk_procedure,
            "service_recording_actions": service_recording_actions_for_mice(
                selected_mice,
                bulk_return_url,
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
    fallback_url = procedure_protocol_url(selected_protocol)
    return_url = safe_return_url(request, fallback_url)
    mouse_ids = selected_mouse_ids(request)

    if not mouse_ids:
        messages.error(
            request,
            "Select at least one mouse before deleting.",
        )
        return redirect(return_url)

    try:
        selected_mice = selected_mice_for_protocol(
            mouse_ids,
            selected_protocol,
        )
    except Mouse.DoesNotExist:
        messages.error(
            request,
            "One or more selected mice could not be found for this protocol.",
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
        delete_url = delete_selected_mice_url(selected_protocol)
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
                    locked_mice = selected_mice_for_protocol(
                        mouse_ids,
                        selected_protocol,
                        for_update=True,
                    )
                    deleted_count = len(locked_mice)

                    for mouse in locked_mice:
                        mouse.delete()
            except Mouse.DoesNotExist:
                messages.error(
                    request,
                    (
                        "One or more selected mice could not be found "
                        "for this protocol."
                    ),
                )
            except ProtectedError:
                messages.error(
                    request,
                    (
                        "Selected mice could not be deleted because another "
                        "record still protects at least one mouse."
                    ),
                )
            else:
                messages.success(
                    request,
                    f"{deleted_count} mice were deleted.",
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


def delete_selected_mice_all(request):
    fallback_url = reverse("lab:mice_page")
    return_url = safe_return_url(request, fallback_url)
    mouse_ids = selected_mouse_ids(request)

    if not mouse_ids:
        messages.error(
            request,
            "Select at least one mouse before deleting.",
        )
        return redirect(return_url)

    try:
        selected_mice = selected_mice_for_ids(mouse_ids)
    except Mouse.DoesNotExist:
        messages.error(
            request,
            "One or more selected mice could not be found.",
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
        delete_url = delete_selected_mice_all_url()
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
                    locked_mice = selected_mice_for_ids(
                        mouse_ids,
                        for_update=True,
                    )
                    deleted_count = len(locked_mice)

                    for mouse in locked_mice:
                        mouse.delete()
            except Mouse.DoesNotExist:
                messages.error(
                    request,
                    "One or more selected mice could not be found.",
                )
            except ProtectedError:
                messages.error(
                    request,
                    (
                        "Selected mice could not be deleted because another "
                        "record still protects at least one mouse."
                    ),
                )
            else:
                messages.success(
                    request,
                    f"{deleted_count} mice were deleted.",
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
            "selected_protocol": None,
            "procedure_count": procedure_count,
            "recording_count": recording_count,
            "return_url": return_url,
        },
    )


def reports_page(request):
    selected_report = request.GET.get("report", "")
    home_office_form = None
    home_office_report = None
    report_query_keys = {
        "licence_reference",
        "start_date",
        "end_date",
        "output_mode",
    }

    if selected_report == HOME_OFFICE_RETURN_REPORT:
        has_report_params = any(
            key in request.GET
            for key in report_query_keys
        )
        home_office_form = HomeOfficeReturnForm(
            request.GET if has_report_params else None
        )

        if has_report_params and home_office_form.is_valid():
            home_office_report = build_home_office_return_report(
                licence_reference=home_office_form.cleaned_data[
                    "licence_reference"
                ],
                start_date=home_office_form.cleaned_data["start_date"],
                end_date=home_office_form.cleaned_data["end_date"],
                output_mode=home_office_form.cleaned_data["output_mode"],
            )

    return render(
        request,
        "lab/reports_page.html",
        {
            "selected_report": selected_report,
            "report_cards": build_report_cards(),
            "home_office_report_key": HOME_OFFICE_RETURN_REPORT,
            "home_office_form": home_office_form,
            "home_office_report": home_office_report,
        },
    )


def home(request):
    return render(
        request,
        "lab/home.html",
        {
            "cards": build_home_cards(request.user),
        },
    )

def protocol_assignment_message(mouse_count, protocol):
    if protocol is None:
        return f"{mouse_count} mice were added with no protocol."

    return (
        f"{mouse_count} mice were added to protocol "
        f"{protocol.protocol_number}."
    )


def add_mouse(request, protocol_number=None, licence_reference=None):
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
    return_with_mouse = bool(
        request.POST.get("return_with_mouse")
        or request.GET.get("return_with_mouse")
    )

    if request.method == "POST":
        form = MouseForm(
            request.POST,
            selected_protocol=selected_protocol,
        )

        if form.is_valid():
            mouse = form.save(commit=False)

            if form.cleaned_data["generate_mouse_id"]:
                mouse.mouse_id = generate_mouse_id(
                    mouse.date_of_birth,
                    mouse.breeding_pair,
                    mouse.tattoo,
                )

            mouse.save()
            form.save_structured_genetics(mouse)

            messages.success(
                request,
                f"Mouse {mouse.mouse_id} was added successfully.",
            )

            if return_with_mouse:
                mouse_ids = selected_recording_mouse_ids_from_url(return_url)

                if mouse.pk not in mouse_ids:
                    mouse_ids.append(mouse.pk)

                return redirect(
                    url_with_query_params(
                        return_url,
                        mice=recording_mice_query_value(mouse_ids),
                        mouse=None,
                    )
                )

            return redirect(return_url)
    else:
        initial = {}

        if selected_protocol is not None:
            initial["protocol"] = selected_protocol

        form = MouseForm(
            initial=initial,
            selected_protocol=selected_protocol,
        )

    context = {
        "form": form,
        "selected_protocol": selected_protocol,
        "return_url": return_url,
        "cancel_url": return_url,
        "return_with_mouse": return_with_mouse,
        "crossing_lookup_url": reverse("lab:crossing_lookup"),
        "crossing_genotype_fields_url": reverse(
            "lab:crossing_genotype_fields"
        ),
    }

    return render(
        request,
        "lab/add_mouse.html",
        context,
    )


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

    if request.method == "POST":
        form = LitterCreateForm(
            request.POST,
            selected_protocol=selected_protocol,
        )

        if form.is_valid():
            with transaction.atomic():
                mice, litter_protocol = create_litter_mice(
                    selected_protocol,
                    form,
                )

            messages.success(
                request,
                protocol_assignment_message(len(mice), litter_protocol),
            )

            if selected_protocol is None:
                return redirect(return_url)

            if litter_protocol == selected_protocol:
                return redirect(return_url)

            return redirect(procedure_protocol_url(litter_protocol))
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
        "selected_protocol": selected_protocol,
        "return_url": return_url,
        "can_add_litter_procedure": can_add_litter_procedure,
        "protocol_procedure_permissions": protocol_procedure_permissions,
        "crossing_lookup_url": reverse("lab:crossing_lookup"),
        "crossing_genotype_fields_url": reverse(
            "lab:crossing_genotype_fields"
        ),
    }

    return render(
        request,
        "lab/add_litter.html",
        context,
    )


def mouse_id_preview(request):
    date_string = request.GET.get("date_of_birth")
    breeding_pair = request.GET.get("breeding_pair", "")
    tattoo = request.GET.get("tattoo", "")

    if not date_string or not breeding_pair or not tattoo:
        return JsonResponse({})

    try:
        date_of_birth = datetime.strptime(
            date_string,
            "%Y-%m-%d",
        ).date()
    except ValueError:
        return JsonResponse({})

    breeding_pair_clean = slugify(breeding_pair).replace("-", "")
    tattoo_clean = tattoo.upper().replace(" ", "")

    base_id = (
        f"{date_of_birth:%Y%m%d}_"
        f"{breeding_pair_clean}_"
        f"{tattoo_clean}"
    )

    final_id = generate_mouse_id(
        date_of_birth,
        breeding_pair,
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
        .filter(mouse_id__icontains=mouse_query)
        .order_by("mouse_id")[:10]
    )

    return JsonResponse(
        {
            "results": [
                {
                    "id": mouse.pk,
                    "label": mouse.mouse_id,
                }
                for mouse in mice
            ],
        }
    )


def mouse_record(request, mouse_pk):
    mouse = get_object_or_404(Mouse, pk=mouse_pk)

    procedures = (
        mouse.procedures
        .select_related("procedure_type")
        .all()
    )

    recordings = (
        Recording.objects
        .filter(mice=mouse)
        .select_related("experiment", "experiment__project", "recording_type")
        .prefetch_related("mice")
        .order_by("recording_type__name", "-recording_date", "sequence_number")
    )
    recordings_by_type = {}

    for recording in recordings:
        recordings_by_type.setdefault(recording.recording_type_id, []).append(
            recording
        )

    recording_types = (
        RecordingType.objects
        .prefetch_related("fields")
        .filter(
            Q(active=True)
            | Q(recordings__mice=mouse)
        )
        .distinct()
        .order_by("name")
    )
    recording_groups = [
        {
            "recording_type": recording_type,
            "recordings": recordings_by_type.get(recording_type.pk, []),
        }
        for recording_type in recording_types
    ]

    return render(
        request,
        "lab/mouse_record.html",
        {
            "mouse": mouse,
            "sections": build_mouse_record_sections(
                mouse,
                procedures,
                recording_groups,
            ),
            "status": mouse.status or "Unknown",
        },
    )
