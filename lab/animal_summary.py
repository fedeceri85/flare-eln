"""Read-only presentations of the animal editor's shared and individual details."""

from datetime import date

from django.db.models import F
from django.urls import reverse

from .forms import BulkMouseUpdateForm
from .models import Procedure, Recording, genotype_category_labels
from .ui import build_recording_type_table, format_value, recording_attachment_counts


def build_animal_recording_sections(mice):
    """List each linked recording once, even when several selected animals share it."""
    recordings = (
        Recording.objects.filter(mice__in=mice).distinct()
        .select_related("experiment__project", "recording_type")
        .prefetch_related("mice", "recording_type__fields")
        .order_by("recording_type__name", "-recording_date", "recording_id")
    )
    recordings = list(recordings)
    attachment_counts = recording_attachment_counts(recordings)
    groups = {}
    for recording in recordings:
        group = groups.setdefault(recording.recording_type_id, {
            "recording_type": recording.recording_type, "recordings": [],
        })
        group["recordings"].append(recording)
    return [
        {
            "title": group["recording_type"].display_plural_name,
            "table": build_recording_type_table(
                group["recording_type"], group["recordings"], include_animals=True, include_notes=True, attachment_counts=attachment_counts,
            ),
        }
        for group in groups.values()
    ]


def summary_field(label, mice, values, keys=None):
    """Keep every animal's value available when a shared field differs."""
    return {
        "label": label,
        "value": values[0],
        "varies": len(set(keys if keys is not None else values)) > 1,
        "animals": [
            {"mouse_id": mouse.pk, "value": value}
            for mouse, value in zip(mice, values)
        ],
    }


def animal_genotype_details(mouse, labels):
    """Use the same complete genotype presentation in summaries and reports."""
    crossing = mouse.crossing_definition
    lines = [membership.line for membership in crossing.crossing_lines.all()] if crossing else []
    line_ids = {line.pk for line in lines}
    calls = {call.mouse_line_id: call for call in mouse.genotype_calls.all()}
    results = []
    for line in lines:
        call = calls.get(line.pk)
        code = call.zygosity if call else "unknown"
        results.append({
            "line_id": line.pk,
            "code": code,
            "label": line.name,
            "value": labels.get(code, code),
            "uncertain": bool(call and call.is_uncertain),
        })
    # Retain historical calls even if they are no longer part of the crossing.
    for call in calls.values():
        if call.mouse_line_id not in line_ids:
            results.append({
                "line_id": call.mouse_line_id,
                "code": call.zygosity,
                "label": call.mouse_line.name,
                "value": labels.get(call.zygosity, call.zygosity),
                "uncertain": call.is_uncertain,
            })
    legacy = mouse.genotype if not crossing else ""
    genotype = "; ".join(
        f"{result['label']}: {result['value']}" + (" (uncertain)" if result["uncertain"] else "")
        for result in results
    )
    if legacy:
        genotype = "; ".join(filter(None, [genotype, legacy]))
    return results, legacy, genotype


def build_animal_summary(mice):
    mice = list(mice)
    if not mice:
        return {}

    groups = []
    for title, names in BulkMouseUpdateForm.MOUSE_UPDATE_GROUPS:
        fields = []
        for name in names:
            raw_values = [getattr(mouse, name) for mouse in mice]
            values = [
                str(format_value(value, "date" if isinstance(value, date) else None))
                for value in raw_values
            ]
            fields.append(summary_field(
                BulkMouseUpdateForm.base_fields[name].label, mice, values,
                keys=[value.pk if hasattr(value, "pk") else value for value in raw_values],
            ))
        groups.append({"title": title, "fields": fields})

    groups[0]["fields"].append(summary_field(
        "Added by", mice,
        [mouse.added_by.get_username() if mouse.added_by else "—" for mouse in mice],
    ))

    labels = genotype_category_labels()
    individual_rows = []
    genotypes = []
    for mouse in mice:
        results, legacy, genotype = animal_genotype_details(mouse, labels)
        genotypes.append(genotype or "—")
        individual_rows.append({
            "mouse_id": mouse.pk,
            "record_url": reverse("lab:animal_summary", args=[mouse.pk]),
            "local_identifier": mouse.tattoo or "—",
            "sex": mouse.get_sex_display() or "Unknown",
            "crossing": mouse.genetics_crossing_display,
            "genotype_results": results,
            "legacy_genotype": legacy,
        })

    return {
        "summary_groups": groups,
        "summary_crossing": summary_field(
            "Crossing", mice,
            [str(mouse.crossing_definition) if mouse.crossing_definition_id else mouse.crossing or "—" for mouse in mice],
            keys=[(mouse.crossing_definition_id, mouse.crossing if not mouse.crossing_definition_id else "") for mouse in mice],
        ),
        "summary_genotype": summary_field("Genotype", mice, genotypes),
        "summary_notes": summary_field("Notes", mice, [mouse.notes or "—" for mouse in mice]),
        "individual_rows": individual_rows,
        "single_mouse_mode": len(mice) == 1,
        "animal_recording_sections": build_animal_recording_sections(mice),
        "procedures": Procedure.objects.filter(mouse__in=mice).select_related("procedure_type").order_by(
            "mouse_id", F("date").desc(nulls_last=True), "-procedure_id",
        ),
    }
