from flare_ai.schemas import ProcedureSearchInput
from lab.models import Procedure, format_postnatal_age
from datetime import timedelta

from django.db.models import DateField, ExpressionWrapper, F, Q


FILTER_LOOKUPS = {
    "mouse_id": "mouse__mouse_id__iexact",
    "mouse_id_contains": "mouse__mouse_id__icontains",
    "procedure_type": "procedure_type__name__icontains",
    "notes_contains": "notes__icontains",
    "procedure_date": "date",
    "procedure_date_from": "date__gte",
    "procedure_date_to": "date__lte",
    "lab_member": "lab_member__icontains"
    }

def recording_age_expression(days):
    return ExpressionWrapper(
        F("recording_date") - timedelta(days=days),
        output_field=DateField(),
    )


def filter_procedures(filters: ProcedureSearchInput):
    procedures = Procedure.objects.all()

    for name, lookup in FILTER_LOOKUPS.items():
        value = getattr(filters, name)
        if value is not None:
            procedures = procedures.filter(**{lookup: value})
    # Keep all animal constraints in one filter call so Animal ID and age must
    # describe the same member of a multi-animal recording.
    animal_conditions = Q()
    if filters.mouse_id is not None:
        animal_conditions &= Q(mouse__mouse_id__iexact=filters.mouse_id)
    if filters.mouse_id_contains is not None:
        animal_conditions &= Q(mouse__mouse_id__icontains=filters.mouse_id_contains)
    if filters.animal_age_days is not None:
        animal_conditions &= Q(
            mouse__date_of_birth=recording_age_expression(filters.animal_age_days)
        )
    if filters.animal_age_days_from is not None:
        animal_conditions &= Q(
            mouse__date_of_birth__lte=recording_age_expression(
                filters.animal_age_days_from
            )
        )
    if filters.animal_age_days_to is not None:
        animal_conditions &= Q(
            mouse__date_of_birth__gte=recording_age_expression(
                filters.animal_age_days_to
            )
        )
    if animal_conditions:
        procedures = procedures.filter(animal_conditions)

    return procedures.distinct()


def search_procedures(filters: ProcedureSearchInput):
    """
    Find procedure by any combination of the following filters:
    - mouse_id: exact match of mouse ID
    - mouse_id_contains: partial match of mouse ID
    - procedure_type: partial match of procedure type
    - notes_contains: partial match of procedure note
    - procedure_date: exact match of procedure date
    - procedure_date_from: procedures on or after this date
    - procedure_date_to: procedures on or before this date
    - lab_member: partial match of lab member name
    Return at most 20 procedures and advise if it has more. 
        When returning the procedures, get the animal IDs and call the search_mice tool to obtain 
        a url of the summary of all the animals that you can provide to the user. 
        This tool never changes records.
    """

    matches = (filter_procedures(filters)
        .select_related("mouse", "procedure_type"))

    procedures = list(matches[:21])

    rows = []
    for procedure in procedures[:20]:
        rows.append({
            "mouse_id": procedure.mouse.mouse_id,
            "procedure_type": procedure.procedure_type.name,
            "procedure_date": procedure.date.isoformat() if procedure.date else None,
            "lab_member": procedure.lab_member,
            "note": procedure.notes,
        })

    return {
        "procedures": rows,
        "has_more": len(procedures)>20
    }
