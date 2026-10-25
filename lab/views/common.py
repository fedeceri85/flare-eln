'Common views and helpers.'

from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)

from django.db.models import F, Prefetch, Q, prefetch_related_objects
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from ..forms import RecordingForm
from ..animal_summary import build_animal_summary
from ..models import (
    CrossingLine,
    Experiment,
    Mouse,
    Project,
    Protocol,
    Recording,
    SERVICES_PROJECT_DESCRIPTION,
    SERVICES_PROJECT_NAME,
)


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


def animal_summary_context(request, mice, *, summary_url, edit_url):
    """Keep animal viewing and edit navigation consistent across entry points."""
    prefetch_related_objects(
        mice, "added_by", "crossing_definition__species", "genotype_calls__mouse_line",
        Prefetch(
            "crossing_definition__crossing_lines",
            queryset=CrossingLine.objects.select_related("line").order_by("position", "crossing_line_id"),
        ),
    )
    active_tab = request.GET.get("mouse_editor_tab", "shared")
    available_tabs = {"shared", "procedures", "services"} | ({"individual"} if len(mice) > 1 else set())
    if active_tab not in available_tabs:
        active_tab = "shared"
    summary_url = url_with_query_params(summary_url, mouse_editor_tab=active_tab)
    summary = build_animal_summary(mice)
    individual_return_url = url_with_query_params(
        summary_url, mouse_editor_tab="shared" if len(mice) == 1 else "individual",
    )
    for row in summary.get("individual_rows", []):
        row["record_url"] = url_with_query_params(row["record_url"], return_url=individual_return_url)
    return {
        **summary,
        "selected_mice": mice,
        "read_only": True,
        "active_tab": active_tab,
        "edit_animals_url": url_with_query_params(
            edit_url, selected_mouse=[mouse.pk for mouse in mice],
            mouse_editor_tab=active_tab, return_url=summary_url,
        ) if mice else "",
    }


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


def experiment_for_project_or_404(project_pk, experiment_pk):
    project = get_object_or_404(Project, pk=project_pk)
    experiment = get_object_or_404(
        Experiment.objects.select_related("recording_type", "project"),
        pk=experiment_pk,
        project=project,
    )
    return project, experiment
