'Projects views and helpers.'

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from ..forms import ExperimentForm, ProjectForm
from ..models import Experiment, Project
from ..ui import (
    EXPERIMENT_FILTER_SPECS,
    PROJECT_FILTER_SPECS,
    apply_filter_specs,
    build_experiments_table,
    build_filter_form,
    build_pagination,
    build_projects_table,
    build_record_count,
    build_service_experiment_cards,
    experiment_page_sortable_fields,
    project_page_sortable_fields,
    read_filter_values,
)
from .common import (
    ensure_services_project,
    experiments_page_url,
    projects_page_url,
    safe_return_url,
)


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
            "has_active_filters": any(filter_values.values()),
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
