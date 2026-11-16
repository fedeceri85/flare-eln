"""Read-only recording searches using explicit Django lookups."""
from django.urls import reverse

from flare_ai.schemas import RecordingSearchInput
from lab.models import Recording
from lab.ui import selected_recording_url

RECORDING_FILTER_LOOKUPS = {
    "owner": "owner__username__icontains",
    "experiment_contains": "experiment__name__icontains",
    "mouse_id": "mice__mouse_id__iexact",
    "mouse_id_contains": "mice__mouse_id__icontains",
    "recording_type": "recording_type__name__icontains",
    "recording_date": "recording_date",
    "recording_date_from": "recording_date__gte",
    "recording_date_to": "recording_date__lte",
    "notes_contains": "notes__icontains",
}

def filter_recordings(filters: RecordingSearchInput):
    recordings = Recording.objects.all()

    for name, lookup in RECORDING_FILTER_LOOKUPS.items():
        value = getattr(filters, name)

        if value is not None:
            recordings = recordings.filter(**{lookup: value})

    return recordings.distinct()



def search_recordings(filters: RecordingSearchInput):
    """Find recording by any combination of the supported properties.
    All filter combined with AND. Text is matched non-semantically, including notes.
    Dates and their bounds are inclusive. 
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
        rows.append({
            "recording_id": recording.recording_id,
            "owner": recording.owner.username if recording.owner else None,
            "experiment": recording.experiment.name,
            "mice": [mouse.mouse_id for mouse in recording.mice.all()],
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
