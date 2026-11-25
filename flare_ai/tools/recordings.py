"""Read-only recording searches using explicit Django lookups."""
from datetime import timedelta

from django.db.models import Count, DateField, ExpressionWrapper, F, Q
from django.urls import reverse

from flare_ai.schemas import RecordingSearchInput
from lab.models import Crossing, Recording, format_postnatal_age
from lab.ui import selected_recording_url

RECORDING_FILTER_LOOKUPS = {
    "owner": "owner__username__icontains",
    "experiment_contains": "experiment__name__icontains",
    "project_contains": "experiment__project__name__icontains",
    "project_id": "experiment__project_id",
    "experiment_id": "experiment_id",
    "recording_type": "recording_type__name__icontains",
    "recording_date": "recording_date",
    "recording_date_from": "recording_date__gte",
    "recording_date_to": "recording_date__lte",
    "notes_contains": "notes__icontains",
}


def recording_age_expression(days):
    return ExpressionWrapper(
        F("recording_date") - timedelta(days=days),
        output_field=DateField(),
    )


def has_age_filter(filters):
    return any(
        value is not None
        for value in (
            filters.animal_age_days,
            filters.animal_age_days_from,
            filters.animal_age_days_to,
        )
    )


def mouse_matches_age_filter(mouse, recording_date, filters):
    if not has_age_filter(filters):
        return None
    age_days = mouse.age_on(recording_date)
    if age_days is None:
        return False
    if filters.animal_age_days is not None and age_days != filters.animal_age_days:
        return False
    if (
        filters.animal_age_days_from is not None
        and age_days < filters.animal_age_days_from
    ):
        return False
    if (
        filters.animal_age_days_to is not None
        and age_days > filters.animal_age_days_to
    ):
        return False
    return True


def filter_recordings(filters: RecordingSearchInput):
    recordings = Recording.objects.all()

    for name, lookup in RECORDING_FILTER_LOOKUPS.items():
        value = getattr(filters, name)

        if value is not None:
            recordings = recordings.filter(**{lookup: value})

    # Independent presence checks: every listed ID must be in the recording,
    # but they need not share properties with the same-mouse block below.
    if filters.mouse_ids is not None:
        for mid in filters.mouse_ids:
            recordings = recordings.filter(mice__mouse_id__iexact=mid)

    # Keep all animal constraints in one filter call so Animal ID, age and
    # property filters must describe the same member of a multi-animal recording.
    animal_conditions = Q()
    if filters.mouse_id is not None:
        animal_conditions &= Q(mice__mouse_id__iexact=filters.mouse_id)
    if filters.mouse_id_contains is not None:
        animal_conditions &= Q(mice__mouse_id__icontains=filters.mouse_id_contains)
    if filters.animal_age_days is not None:
        animal_conditions &= Q(
            mice__date_of_birth=recording_age_expression(filters.animal_age_days)
        )
    if filters.animal_age_days_from is not None:
        animal_conditions &= Q(
            mice__date_of_birth__lte=recording_age_expression(
                filters.animal_age_days_from
            )
        )
    if filters.animal_age_days_to is not None:
        animal_conditions &= Q(
            mice__date_of_birth__gte=recording_age_expression(
                filters.animal_age_days_to
            )
        )

    # --- mouse property filters (same-mouse) ---
    if filters.crossing is not None:
        text = filters.crossing
        animal_conditions &= (
            Q(mice__crossing_definition__display_name__icontains=text)
            | Q(mice__crossing_definition__lines__name__icontains=text)
            | Q(mice__crossing_definition__lines__aliases__icontains=text)
            | Q(mice__crossing_definition__isnull=True, mice__crossing__icontains=text)
        )

    if filters.crossing_type is not None:
        crossings = Crossing.objects.annotate(
            mutant_lines=Count("lines", filter=Q(lines__is_wild_type=False))
        )
        if filters.crossing_type == "WT":
            crossings = crossings.filter(mutant_lines=0)
        elif filters.crossing_type == "Single":
            crossings = crossings.filter(mutant_lines=1)
        else:
            crossings = crossings.filter(mutant_lines__gte=2)
        legacy_types = [filters.crossing_type]
        if filters.crossing_type == "Multiple":
            legacy_types.append("Double_triple")
        animal_conditions &= (
            Q(mice__crossing_definition_id__in=crossings.values("pk"))
            | Q(mice__crossing_definition__isnull=True, mice__crossing_type__in=legacy_types)
        )

    if filters.sex is not None:
        value = "" if filters.sex == "unknown" else filters.sex
        animal_conditions &= Q(mice__sex=value)

    if filters.colour is not None:
        animal_conditions &= Q(mice__colour__icontains=filters.colour)

    if filters.genotype is not None:
        animal_conditions &= (
            Q(mice__genotype__icontains=filters.genotype)
            | Q(mice__genotype_calls__zygosity__icontains=filters.genotype)
            | Q(mice__genotype_calls__mouse_line__name__icontains=filters.genotype)
        )

    if filters.species is not None:
        animal_conditions &= Q(
            mice__crossing_definition__species__name__icontains=filters.species
        )

    if filters.breeding_pair is not None:
        animal_conditions &= Q(mice__breeding_pair__icontains=filters.breeding_pair)

    if filters.status is not None:
        animal_conditions &= Q(mice__status=filters.status)

    if filters.severity is not None:
        value = "" if filters.severity == "unknown" else filters.severity
        animal_conditions &= Q(mice__severity=value)

    if animal_conditions:
        recordings = recordings.filter(animal_conditions)

    return recordings.distinct()


def search_recordings(filters: RecordingSearchInput):
    """Find recordings by any combination of the supported properties.
    All filters combine with AND. Text is matched non-semantically, including notes.
    Dates and age bounds are inclusive. Animal age means postnatal days on the
    recording date; convert weeks to days before calling this tool. A recording
    matches an age filter when at least one linked animal matches, and Animal ID,
    age and mouse property filters (crossing, sex, genotype, species, colour,
    breeding pair, status, severity, crossing type) always refer to the same
    linked animal. Use mouse_ids to require that several specific animals are
    all present in the recording; it is independent of the property filters.
    Return at most 20 recordings and advise if it has more. 
    When returning the recordings, get the animal IDs and call the search_mice tool to obtain 
    a url of the summary of all the animals that you can provide to the user. 
    This tool never changes records. """


    matches = (
        filter_recordings(filters)
        .select_related("owner", "experiment__project", "recording_type")
        .prefetch_related("mice")
        .order_by("-recording_date", "-recording_id")
    )

    recordings = list(matches[:21])

    rows = []
    for recording in recordings[:20]:
        mice = sorted(
            recording.mice.all(), key=lambda mouse: mouse.mouse_id.lower()
        )
        rows.append({
            "recording_id": recording.recording_id,
            "owner": recording.owner.username if recording.owner else None,
            "experiment": recording.experiment.name,
            "mice": [mouse.mouse_id for mouse in mice],
            "animal_ages": [
                {
                    "mouse_id": mouse.mouse_id,
                    "age_days": mouse.age_on(recording.recording_date),
                    "age_display": format_postnatal_age(
                        mouse.age_on(recording.recording_date)
                    ),
                    "age_filter_match": mouse_matches_age_filter(
                        mouse, recording.recording_date, filters
                    ),
                }
                for mouse in mice
            ],
            "recording_type": recording.recording_type.name,
            "recording_date": recording.recording_date.isoformat(),
            "data_paths": recording.data_paths,
            "notes": recording.notes,
            "values": recording.values,
            "url": selected_recording_url(recording),
            "experiment_url": reverse(
                "lab:recordings_page",
                args=[
                    recording.experiment.project_id,
                    recording.experiment_id,
                ],
            ),
        })

    return {
        "recordings": rows,
        "has_more": len(recordings) > 20,
    }
