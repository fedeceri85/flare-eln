"""Read-only project searches using explicit Django lookups."""

from django.db.models import Count
from django.urls import reverse

from flare_ai.schemas import ProjectSearchInput
from lab.models import Project

PROJECT_FILTER_LOOKUPS = {
    "name": "name__icontains",
    "lab_member": "lab_member__icontains",
    "description": "description__icontains",
    "active": "active",
    "is_service": "is_service",
    "start_date_from": "start_date__gte",
    "start_date_to": "start_date__lte",
    "end_date_from": "end_date__gte",
    "end_date_to": "end_date__lte",
}


def filter_projects(filters: ProjectSearchInput):
    projects = Project.objects.annotate(experiment_count=Count("experiments"))

    for name, lookup in PROJECT_FILTER_LOOKUPS.items():
        value = getattr(filters, name)
        if value is not None:
            projects = projects.filter(**{lookup: value})

    # The synthetic Services project is a shared container rather than a
    # research project, so hide it unless service projects are requested.
    if filters.is_service is None:
        projects = projects.filter(is_service=False)

    return projects.order_by("name", "project_id")


def search_projects(filters: ProjectSearchInput):
    """Find research projects by any combination of the supported properties.

    All filters combine with AND. Text is matched literally, ignoring case.
    Date bounds are inclusive. Returns at most 20 projects and has_more, not a
    complete count. Each result includes the project details, its experiment
    count and a link to its experiments page. Lab-wide service projects are
    excluded unless is_service is true. This tool never changes records.
    """

    matches = filter_projects(filters)
    projects = list(matches[:21])

    rows = []
    for project in projects[:20]:
        rows.append({
            "project_id": project.project_id,
            "name": project.name,
            "description": project.description,
            "lab_member": project.lab_member,
            "active": project.active,
            "start_date": project.start_date.isoformat() if project.start_date else None,
            "end_date": project.end_date.isoformat() if project.end_date else None,
            "experiment_count": project.experiment_count,
            "url": reverse("lab:experiments_page", args=(project.project_id,)),
        })

    return {
        "projects": rows,
        "has_more": len(projects) > 20,
    }
