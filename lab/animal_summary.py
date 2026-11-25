"""Read-only presentations of the animal editor's shared and individual details."""

from datetime import date

from django.db.models import F
from django.urls import reverse

from .forms import BulkMouseUpdateForm
from .models import Procedure, Recording, format_postnatal_age, genotype_category_labels
from .ui import build_animal_recording_table, format_value, recording_attachment_counts


def build_animal_recording_sections(mice, user=None):
    """Group each linked recording under its type, project, and experiment.

    Mirrors the main recordings table's hierarchy so the animal summary shows the
    same columns, expandable animal rows, and row actions for every recording
    linked to the selected animals (each recording appears once).
    """
    recordings = (
        Recording.objects.filter(mice__in=mice).distinct()
        .select_related("experiment__project", "recording_type")
        .prefetch_related(
            "mice", "recording_type__fields",
            "mice__crossing_definition", "mice__crossing_definition__crossing_lines__line",
            "mice__genotype_calls", "mice__genotype_calls__mouse_line",
        )
        .order_by(
            "recording_type__name", "experiment__project__name", "experiment__name",
            "-recording_date", "recording_id",
        )
    )
    recordings = list(recordings)
    attachment_counts = recording_attachment_counts(recordings)

    types = {}
    for recording in recordings:
        type_group = types.setdefault(recording.recording_type_id, {
            "recording_type": recording.recording_type,
            "type_title": recording.recording_type.display_plural_name,
            "projects": {},
        })
        project_group = type_group["projects"].setdefault(recording.experiment.project_id, {
            "project": recording.experiment.project,
            "project_title": str(recording.experiment.project),
            "experiments": {},
        })
        experiment_group = project_group["experiments"].setdefault(recording.experiment_id, {
            "experiment": recording.experiment,
            "experiment_title": recording.experiment.name,
            "recordings": [],
        })
        experiment_group["recordings"].append(recording)

    sections = []
    for type_group in types.values():
        projects = []
        for project_group in type_group["projects"].values():
            experiments = []
            for experiment_group in project_group["experiments"].values():
                experiments.append({
                    "experiment": experiment_group["experiment"],
                    "experiment_title": experiment_group["experiment_title"],
                    "table": build_animal_recording_table(
                        type_group["recording_type"], experiment_group["recordings"], user,
                        attachment_counts=attachment_counts,
                    ),
                })
            projects.append({
                "project": project_group["project"],
                "project_title": project_group["project_title"],
                "experiments": experiments,
            })
        sections.append({
            "recording_type": type_group["recording_type"],
            "type_title": type_group["type_title"],
            "projects": projects,
        })
    return sections


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


def build_animal_summary(mice, user=None):
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

    procedures = list(
        Procedure.objects.filter(mouse__in=mice)
        .select_related("procedure_type", "mouse")
        .order_by("mouse_id", F("date").desc(nulls_last=True), "-procedure_id")
    )
    for procedure in procedures:
        procedure.age_display = format_postnatal_age(procedure.mouse.age_on(procedure.date))

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
        "animal_recording_sections": build_animal_recording_sections(mice, user),
        "procedures": procedures,
    }
