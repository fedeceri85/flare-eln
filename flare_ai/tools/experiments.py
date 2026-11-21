"""Read-only experiment searches using explicit Django lookups."""

from django.db.models import Count
from django.urls import reverse

from flare_ai.schemas import ExperimentSearchInput
from lab.models import Experiment

EXPERIMENT_FILTER_LOOKUPS = {
    "name": "name__icontains",
    "description": "description__icontains",
    "project": "project__name__icontains",
    "project_id": "project_id",
    "recording_type": "recording_type__name__icontains",
    "archived": "archived",
    "is_service": "project__is_service",
    "start_date_from": "start_date__gte",
    "start_date_to": "start_date__lte",
}


def filter_experiments(filters: ExperimentSearchInput):
    experiments = (
        Experiment.objects
        .select_related("project", "recording_type")
        .annotate(recording_count=Count("recordings"))
    )

    for name, lookup in EXPERIMENT_FILTER_LOOKUPS.items():
        value = getattr(filters, name)
        if value is not None:
            experiments = experiments.filter(**{lookup: value})

    # Service experiments live under the lab-wide Services project, which has
    # its own page; hide them unless service experiments are requested.
    if filters.is_service is None:
        experiments = experiments.filter(project__is_service=False)

    return experiments.order_by("project__name", "name", "experiment_id")


def search_experiments(filters: ExperimentSearchInput):
    """Find experiments by any combination of the supported properties.

    All filters combine with AND. Text is matched literally, ignoring case.
    Date bounds are inclusive. Use project for a partial project name, or
    project_id to pin one project returned by search_projects. Returns at most
    20 experiments and has_more, not a complete count. Each result includes the
    experiment details, its project, recording type, recording count and a link
    to its recordings page. Lab-wide service experiments are excluded unless
    is_service is true. This tool never changes records.
    """

    matches = filter_experiments(filters)
    experiments = list(matches[:21])

    rows = []
    for experiment in experiments[:20]:
        rows.append({
            "experiment_id": experiment.experiment_id,
            "name": experiment.name,
            "description": experiment.description,
            "project_id": experiment.project_id,
            "project": experiment.project.name,
            "recording_type": experiment.recording_type.name if experiment.recording_type else None,
            "start_date": experiment.start_date.isoformat() if experiment.start_date else None,
            "end_date": experiment.end_date.isoformat() if experiment.end_date else None,
            "archived": experiment.archived,
            "recording_count": experiment.recording_count,
            "url": reverse(
                "lab:recordings_page",
                args=(experiment.project_id, experiment.experiment_id),
            ),
        })

    return {
        "experiments": rows,
        "has_more": len(experiments) > 20,
    }
