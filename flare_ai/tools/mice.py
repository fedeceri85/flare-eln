"""Read-only animal searches using an explicit set of Django lookups."""

from urllib.parse import urlencode

from django.db.models import Count, Exists, F, OuterRef, Q
from django.db.models.fields.json import KeyTransform
from django.urls import reverse

from flare_ai.schemas import MouseSearchInput
from lab.models import Crossing, GenotypeCategory, Mouse, MouseGenotype, SEVERITY_RANK

# Only server-defined lookup paths reach the ORM; the model supplies values.
FILTER_LOOKUPS = {
    "mouse_id": "mouse_id__iexact",
    "mouse_id_contains": "mouse_id__icontains",
    "id_status": "id_status",
    "local_identifier": "tattoo__iexact",
    "marking_type": "marking_type",
    "colour": "colour__icontains",
    "sex": "sex",
    "status": "status",
    "severity": "severity",
    "breeding_pair": "breeding_pair__icontains",
    "notes": "notes__icontains",
    "date_of_birth": "date_of_birth",
    "born_from": "date_of_birth__gte",
    "born_to": "date_of_birth__lte",
    "date_added": "date_added",
    "added_from": "date_added__gte",
    "added_to": "date_added__lte",
    "protocol_start_date": "protocol_start_date",
    "protocol_start_from": "protocol_start_date__gte",
    "protocol_start_to": "protocol_start_date__lte",
    "cull_date": "cull_date",
    "culled_from": "cull_date__gte",
    "culled_to": "cull_date__lte",
    "crossing_id": "crossing_definition_id",
    "species": "crossing_definition__species__name__icontains",
    "genotype": "genotype__icontains",
    "protocol": "protocol__name__icontains",
    "protocol_id": "protocol_id",
    "protocol_number": "protocol__protocol_number",
    "licence_reference": "protocol__licence_reference__icontains",
    "added_by_id": "added_by_id",
    "mother_id": "mother__mouse_id__iexact",
    "father_id": "father__mouse_id__iexact",
    "mother_external_id": "mother_external_id__iexact",
    "father_external_id": "father_external_id__iexact",
    "mouse_ids": "mouse_id__in",
}

SORT_FIELDS = {
    "date_added": "date_added",
    "date_of_birth": "date_of_birth",
    "mouse_id": "mouse_id",
}

def missing_value(field_name):
    """Missing means NULL, empty text, or an empty metadata object."""
    condition = Q(**{f"{field_name}__isnull": True})
    field = Mouse._meta.get_field(field_name)
    if field.get_internal_type() in {"CharField", "TextField"}:
        condition |= Q(**{field_name: ""})
    elif field_name == "custom_metadata":
        condition |= Q(custom_metadata={}) | Q(custom_metadata=None)
    return condition


def filter_mice(filters: MouseSearchInput):
    mice = Mouse.objects.all()
    for name, lookup in FILTER_LOOKUPS.items():
        value = getattr(filters, name)
        if value is not None:
            if name in {"sex", "severity"} and value == "unknown":
                value = ""
            mice = mice.filter(**{lookup: value})

    if filters.crossing is not None:
        text = filters.crossing
        mice = mice.filter(
            Q(crossing_definition__display_name__icontains=text)
            | Q(crossing_definition__lines__name__icontains=text)
            | Q(crossing_definition__lines__aliases__icontains=text)
            | Q(crossing_definition__isnull=True, crossing__icontains=text)
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
        mice = mice.filter(
            Q(crossing_definition_id__in=crossings.values("pk"))
            | Q(crossing_definition__isnull=True, crossing_type__in=legacy_types)
        )

    for genotype in filters.genotypes:
        # Each EXISTS matches one call, keeping line/category/uncertainty together.
        calls = MouseGenotype.objects.filter(mouse_id=OuterRef("pk"))
        if genotype.line is not None:
            calls = calls.filter(
                Q(mouse_line__name__icontains=genotype.line)
                | Q(mouse_line__aliases__icontains=genotype.line)
            )
        if genotype.category is not None:
            codes = GenotypeCategory.objects.filter(
                Q(code__iexact=genotype.category) | Q(label__iexact=genotype.category)
            ).values("code")
            calls = calls.filter(Q(zygosity__iexact=genotype.category) | Q(zygosity__in=codes))
        if genotype.uncertain is not None:
            calls = calls.filter(is_uncertain=genotype.uncertain)
        mice = mice.filter(Exists(calls))

    if filters.added_by is not None:
        text = filters.added_by
        mice = mice.filter(
            Q(added_by__username__icontains=text)
            | Q(added_by__first_name__icontains=text)
            | Q(added_by__last_name__icontains=text)
        )

    if filters.severity_exceeded is not None:
        exceeded = Q(pk__in=[])
        for severity, rank in SEVERITY_RANK.items():
            lower = [name for name, other_rank in SEVERITY_RANK.items() if other_rank < rank]
            exceeded |= Q(severity=severity, protocol__max_severity__in=lower)
        mice = mice.filter(exceeded) if filters.severity_exceeded else mice.exclude(exceeded)

    for key, value in (filters.custom_metadata or {}).items():
        # Treat the entire key as data, including '__' or SQL-like text.
        mice = mice.alias(ai_metadata_value=KeyTransform(key, "custom_metadata")).filter(
            ai_metadata_value=value
        )

    for field_name in filters.missing:
        mice = mice.filter(missing_value(field_name))
    for field_name in filters.present:
        mice = mice.exclude(missing_value(field_name))
    return mice.distinct()


def search_mice(filters: MouseSearchInput) -> dict:
    """Find mice by any combination of the supported mouse properties.

    All filters combine with AND. Text is matched literally, ignoring case,
    not semantically or with spelling correction. Each genotypes entry matches
    one recorded line/category/uncertainty call; multiple entries can require
    different lines. Dates and their bounds are inclusive. Returns at most 20
    mice and has_more, not a complete count. Each compact result includes the
    Animal ID, birth date, crossing, genotype summary and record URL. Other
    properties can be filtered but are not included in this compact response.
    When search_mice returns a summary_url, offer it as a Markdown link named
    "View animal summary". Use the exact returned URL without modifying it.
    This tool never changes records.

    """

    sort_field = SORT_FIELDS[filters.sort_by]

    if filters.sort_direction == "desc":
        ordering = F(sort_field).desc(nulls_last=True)
    else:
        ordering = F(sort_field).asc(nulls_last=True)


    matches = filter_mice(filters).select_related(
        "crossing_definition", "protocol"
    ).prefetch_related(
        "genotype_calls__mouse_line", "procedures__procedure_type"
    ).order_by(ordering, "mouse_id")
    mice = list(matches[:21])
    summary_url = None
    if mice:
        summary_url = reverse("lab:view_selected_mice_all") + "?" + urlencode(
            {
                "selected_mouse": [
                    mouse.pk
                    for mouse in mice[:20]
                ]
            },
            doseq=True,
        )
        
    labels = dict(GenotypeCategory.objects.values_list("code", "label")) if mice else {}

    rows = []
    for mouse in mice[:20]:
        genotype = "; ".join(
            f"{call.mouse_line.name}: {labels.get(call.zygosity, call.zygosity)}"
            + (" (uncertain)" if call.is_uncertain else "")
            for call in mouse.genotype_calls.all()
        ) or mouse.genotype
        rows.append({
            "mouse_id": mouse.mouse_id,
            "date_of_birth": mouse.date_of_birth.isoformat() if mouse.date_of_birth else None,
            "crossing": mouse.genetics_crossing_display,
            "procedures": [
                {
                    "lab_member": procedure.lab_member,
                    "date": procedure.date.isoformat() if procedure.date else None,
                    "procedure_type": procedure.procedure_type.name,
                }
                for procedure in mouse.procedures.all()
            ],
            "genotype": genotype,
            "url": reverse("lab:animal_summary", args=(mouse.mouse_id,)),
            "protocol": (
                {
                    "protocol_id": mouse.protocol_id,
                    "protocol_number": mouse.protocol.protocol_number,
                    "licence_reference": mouse.protocol.licence_reference,
                    "name": mouse.protocol.name,
                }
                if mouse.protocol
                else None
            ),
        })

    return {
        "mice": rows,
        "has_more": len(mice) > 20,
        "summary_url": summary_url,
    }
