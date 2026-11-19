"""Prepare review-only drafts for the existing Add animals form."""

import re
import time
from typing import TypeAlias
from urllib.parse import urlencode
from uuid import uuid4

from django.db.models import Q
from django.db.models.query import QuerySet
from django.urls import reverse
from pydantic_ai import RunContext

from flare_ai.context import AIRequestContext, PageContext
from flare_ai.schemas import AnimalFormDraftInput
from lab.forms import (
    ANIMAL_FORM_DRAFT_LIMIT,
    ANIMAL_FORM_DRAFT_MAX_AGE,
    ANIMAL_FORM_DRAFT_SESSION_KEY,
    LitterCreateForm,
    active_crossings_queryset,
    crossing_choice_label,
    genotype_field_name,
)
from lab.models import Crossing, GenotypeCategory, Mouse, MouseLine, Protocol


Issue: TypeAlias = dict[str, object]
ProtocolResolution: TypeAlias = tuple[Protocol | None, Issue | None]
CrossingResolution: TypeAlias = tuple[Crossing | None, Issue | None]
ResolvedGenotypeRow: TypeAlias = tuple[dict[int, str], set[int]]
ResolvedGenotypes: TypeAlias = dict[str, ResolvedGenotypeRow]
GenotypeData: TypeAlias = tuple[list[MouseLine], ResolvedGenotypes]
GenotypeResolution: TypeAlias = tuple[GenotypeData | None, Issue | None]


def _protocol_for(
    draft: AnimalFormDraftInput, page: PageContext
) -> ProtocolResolution:
    """Resolve exactly one protocol or return a structured input issue.

    Explicit draft identifiers take precedence over protocol context inferred
    from the current page. Ambiguous searches return up to ten candidates.
    """
    matches: QuerySet[Protocol]
    if draft.protocol_id is not None:
        matches = Protocol.objects.filter(pk=draft.protocol_id)
    elif draft.protocol_number is not None:
        matches = Protocol.objects.filter(protocol_number=draft.protocol_number)
        if draft.licence_reference:
            matches = matches.filter(licence_reference__iexact=draft.licence_reference)
    elif draft.protocol_query:
        query = draft.protocol_query
        matches = Protocol.objects.filter(
            Q(name__icontains=query) | Q(licence_reference__icontains=query)
        )
    elif page.records.get("protocol_id"):
        matches = Protocol.objects.filter(pk=page.records["protocol_id"])
    else:
        return None, {"status": "needs_input", "missing_required": ["protocol"]}

    choices: list[Protocol] = list(
        matches.order_by("licence_reference", "protocol_number", "pk")[:11]
    )
    if len(choices) != 1:
        return None, {
            "status": "needs_input",
            "message": "Choose one protocol.",
            "protocol_candidates": [
                {
                    "id": protocol.pk,
                    "number": protocol.protocol_number,
                    "licence_reference": protocol.licence_reference,
                    "name": protocol.name,
                }
                for protocol in choices[:10]
            ],
        }
    return choices[0], None


def _crossing_for(draft: AnimalFormDraftInput) -> CrossingResolution:
    """Resolve one active crossing or return candidates for user selection.

    A valid primary key wins immediately. Text searches cover the display name,
    species, line names and aliases, while preferring an exact display label.
    """
    crossings: QuerySet[Crossing] = active_crossings_queryset()
    if draft.crossing_id is not None:
        crossing = crossings.filter(pk=draft.crossing_id).first()
        if crossing is not None:
            return crossing, None
        return None, {"status": "needs_input", "message": "Choose an active crossing."}
    if not draft.crossing:
        return None, {"status": "needs_input", "missing_required": ["crossing"]}

    query = draft.crossing.strip()
    candidates: list[Crossing] = list(
        crossings.filter(
            Q(display_name__icontains=query)
            | Q(species__name__icontains=query)
            | Q(lines__name__icontains=query)
            | Q(lines__aliases__icontains=query)
        ).distinct()[:11]
    )
    exact: list[Crossing] = [
        crossing for crossing in candidates
        if query.casefold() in {
            crossing.display_name.casefold(),
            crossing_choice_label(crossing).casefold(),
        }
    ]
    if len(exact) == 1:
        return exact[0], None
    if len(candidates) == 1:
        return candidates[0], None
    return None, {
        "status": "needs_input",
        "message": "Choose one active crossing.",
        "crossing_candidates": [
            {"id": crossing.pk, "label": crossing_choice_label(crossing)}
            for crossing in candidates[:10]
        ],
    }


def _genotypes_for(
    draft: AnimalFormDraftInput, crossing: Crossing
) -> GenotypeResolution:
    """Normalize supplied genotype labels against a crossing's active catalogue.

    The successful result contains ordered lines and per-row mappings of line
    IDs to category codes plus uncertain line IDs. Unknown or ambiguous terms
    are collected into one structured issue so the user can correct them.
    """
    lines: list[MouseLine] = crossing.ordered_lines()
    line_terms: dict[str, list[MouseLine]] = {}
    for line in lines:
        for term in [line.name, *re.split(r"[,;\n]", line.aliases)]:
            if term.strip():
                matches = line_terms.setdefault(term.strip().casefold(), [])
                if line not in matches:
                    matches.append(line)
    categories: dict[str, str] = {}
    for category in GenotypeCategory.objects.filter(active=True):
        categories[category.code.casefold()] = category.code
        categories[category.label.casefold()] = category.code

    resolved_rows: ResolvedGenotypes = {}
    problems: list[str] = []
    sources: list[tuple[str, dict[str, str], list[str]]] = [
        ("shared", draft.shared_genotypes, draft.shared_uncertain_genotypes)
    ]
    sources.extend((row.number, row.genotypes, row.uncertain_genotypes) for row in draft.animals)
    for source, values, uncertain in sources:
        resolved: dict[int, str] = {}
        uncertain_ids: set[int] = set()
        for term, result in values.items():
            matches = line_terms.get(term.casefold(), [])
            code = categories.get(result.casefold())
            if len(matches) != 1:
                problems.append(f"Genotype line {term!r} does not identify one line in this crossing.")
            elif code is None:
                problems.append(f"Genotype result {result!r} is not an active category.")
            else:
                resolved[matches[0].pk] = code
        for term in uncertain:
            matches = line_terms.get(term.casefold(), [])
            if len(matches) != 1:
                problems.append(f"Uncertain genotype line {term!r} does not identify one line in this crossing.")
            else:
                uncertain_ids.add(matches[0].pk)
        resolved_rows[source] = (resolved, uncertain_ids)
    if problems:
        return None, {
            "status": "needs_input",
            "message": "Resolve the genotype descriptions before preparing the form.",
            "problems": list(dict.fromkeys(problems)),
            "available_lines": [line.name for line in lines],
            "available_results": [
                {"code": category.code, "label": category.label}
                for category in GenotypeCategory.objects.filter(active=True)
            ],
        }
    return (lines, resolved_rows), None


def prepare_animal_form(
    ctx: RunContext[AIRequestContext], draft: AnimalFormDraftInput
) -> dict:
    """Prepare, but never submit, the existing Add animals form.

    Collect protocol, crossing and Animal ID information before calling. Optional
    fields may stay blank when the user says they have no more information. Do
    not infer dates, sex, genotype, parents or scientific values. If an entered
    parent is not found, call again with keep_unlinked_mother/father=true only
    after the user explicitly confirms it should be kept as external text.
    The returned URL must be shown exactly as supplied.
    If the crossing is not found, search the crossing using partial matching
    of the provided text before addressing the user again.
    If the user does not have an ID for the animals, do not ask the user for a prefix,
    just generate one using the pre-determined rules.
    """

    if ctx.deps.session is None:
        return {"status": "invalid", "message": "Form drafts are unavailable in this session."}

    protocol, issue = _protocol_for(draft, ctx.deps.page)
    if issue:
        return issue
    crossing, issue = _crossing_for(draft)
    if issue:
        return issue
    genotype_data, issue = _genotypes_for(draft, crossing)
    if issue:
        return issue
    lines, resolved_genotypes = genotype_data

    form_data = {
        "protocol": str(protocol.pk),
        "crossing_definition": str(crossing.pk),
        "crossing_search": crossing_choice_label(crossing),
        "mouse_id_prefix": draft.mouse_id_prefix,
        "mouse_numbers": draft.mouse_numbers,
        "status": draft.status,
        "breeding_pair": draft.breeding_pair,
        "colour": draft.colour,
        "notes": draft.notes,
        "genotype_modes_present": "1",
    }
    if draft.generate_ids:
        form_data["no_id_available"] = "on"
    for name in ("date_of_birth", "protocol_start_date", "cull_date"):
        value = getattr(draft, name)
        if value is not None:
            form_data[name] = value.isoformat()
    if draft.severity:
        form_data["severity"] = draft.severity

    if not draft.mouse_id_prefix and not draft.mouse_numbers:
        return {
            "status": "needs_input",
            "missing_required": ["Animal ID or litter number/range"],
        }
    probe = LitterCreateForm(form_data)
    if probe.numbers_error:
        return {"status": "invalid", "errors": {"mouse_numbers": [probe.numbers_error]}}
    if not probe.numbers:
        return {
            "status": "needs_input",
            "missing_required": ["Animal ID or litter number/range"],
        }
    numbers = set(probe.numbers)
    row_inputs = {}
    for row in draft.animals:
        if row.number not in numbers:
            return {
                "status": "invalid",
                "message": f"Animal row {row.number!r} is not present in the supplied number/range.",
            }
        if row.number in row_inputs:
            return {"status": "invalid", "message": f"Animal row {row.number!r} is listed more than once."}
        row_inputs[row.number] = row

    for role in ("mother", "father"):
        value = getattr(draft, f"{role}_id")
        if not value:
            continue
        exact = Mouse.objects.filter(pk=value).first()
        insensitive = list(Mouse.objects.filter(pk__iexact=value).order_by("pk")[:3])
        match = exact or (insensitive[0] if len(insensitive) == 1 else None)
        if match is None and not getattr(draft, f"keep_unlinked_{role}"):
            result = {
                "status": "needs_input",
                "message": f"The {role} ID {value!r} was not found. Confirm whether to keep it as unlinked external text.",
            }
            if insensitive:
                result["parent_candidates"] = [mouse.pk for mouse in insensitive]
            return result
        form_data[f"{role}_external_id"] = value
        if match is not None:
            form_data[role] = match.pk

    shared_values, shared_uncertain = resolved_genotypes["shared"]
    for line in lines:
        name = genotype_field_name(line)
        row_has_value = any(line.pk in resolved_genotypes[number][0] for number in row_inputs)
        row_has_uncertainty = any(line.pk in resolved_genotypes[number][1] for number in row_inputs)
        if row_has_value or row_has_uncertainty:
            form_data[f"{name}_individual"] = "on"
            for number in probe.numbers:
                values, uncertain = resolved_genotypes.get(number, ({}, set()))
                form_data[f"pup_{number}_{name}"] = values.get(
                    line.pk, shared_values.get(line.pk, "unknown")
                )
                if line.pk in uncertain or (line.pk in shared_uncertain and line.pk not in values):
                    form_data[f"pup_{number}_{name}_uncertain"] = "on"
        elif line.pk in shared_values:
            form_data[name] = shared_values[line.pk]
            if line.pk in shared_uncertain:
                form_data[f"{name}_uncertain"] = "on"

    for number, row in row_inputs.items():
        form_data[f"pup_{number}_marking"] = row.local_identifier
        if row.sex and row.sex != "unknown":
            form_data[f"pup_{number}_sex"] = row.sex

    form = LitterCreateForm(form_data)
    if not form.is_valid():
        return {
            "status": "invalid",
            "errors": {
                name: [entry["message"] for entry in entries]
                for name, entries in form.errors.get_json_data(escape_html=True).items()
            },
        }

    now = time.time()
    drafts = ctx.deps.session.get(ANIMAL_FORM_DRAFT_SESSION_KEY, {})
    if not isinstance(drafts, dict):
        drafts = {}
    drafts = {
        key: value for key, value in drafts.items()
        if isinstance(value, dict)
        and isinstance(value.get("created_at"), (int, float))
        and now - value.get("created_at", 0) <= ANIMAL_FORM_DRAFT_MAX_AGE
    }
    draft_id = str(uuid4())
    drafts[draft_id] = {
        "user_id": str(ctx.deps.user.pk),
        "created_at": now,
        "data": dict(form.data),
    }
    while len(drafts) > ANIMAL_FORM_DRAFT_LIMIT:
        drafts.pop(next(iter(drafts)))
    ctx.deps.session[ANIMAL_FORM_DRAFT_SESSION_KEY] = drafts
    ctx.deps.session.modified = True

    params = {"ai_draft": draft_id}
    if ctx.deps.page.path.startswith("/"):
        params["return_url"] = ctx.deps.page.path
    url = reverse("lab:add_litter_all") + "?" + urlencode(params)
    return {
        "status": "ready",
        "url": url,
        "summary": {
            "animal_count": len(form.numbers),
            "protocol": f"{protocol.licence_reference} / {protocol.protocol_number} — {protocol.name}",
            "crossing": crossing_choice_label(crossing),
        },
        "message": "The user must review the populated form and press Save; no animals were created.",
    }
