"""Versioned, portable mapping definitions. No rows, animals or approvals here."""
import difflib
import re
from datetime import date, datetime

from django.core.exceptions import ValidationError

from ..models import Crossing, GenotypeCategory, MouseLine, Protocol, Species
from .source import binding, resolve_binding
from .procedures import procedure_definitions


ANIMAL_FIELDS = [
    ("identifier", "Animal ID (unique)", ["mouse id", "mouseid", "pedigree #", "animal id", "animal identifier"]),
    ("date_of_birth", "Birth date", ["birth date", "dob", "date of birth"]),
    ("tattoo", "Local identifier (tattoo, ear clip)", ["ear tag", "tattoo", "ear clip", "local id", "local identifier"]),
    ("breeding_pair", "Breeding pair", ["breeding pair"]),
    ("sex", "Sex", ["sex"]),
    ("crossing", "Strain / crossing", ["strain", "crossing"]),
    ("notes", "Animal notes", ["animal notes"]),
    ("status", "Animal status", ["status"]),
    ("cull_date", "Cull date", ["cull date", "culling date", "date culled", "date of cull", "date of culling"]),
    ("severity", "Severity experienced", ["severity", "severity experienced", "severity experienced by the animal", "actual severity"]),
]


def normalize(value):
    return re.sub(r"[^\w]+", "", str(value).casefold())


def targets(experiment):
    result = [
        ("recording_date", "Recording date", ["date", "recording date"]),
        ("data_paths", "Recording paths", ["folder", "path", "data paths"]),
        ("notes", "Recording notes", ["notes"]),
    ]
    for field in experiment.recording_type.fields.filter(active=True):
        if experiment.recording_type.slug == "genotyping" and field.key == "crossing":
            continue
        result.append((f"field:{field.key}", field.label, [field.label, field.key]))
    result.extend((f"animal:{key}", label, hints) for key, label, hints in ANIMAL_FIELDS)
    return result


def new_definition(experiment, columns):
    definition = {
        "version": 2, "recording_type": experiment.recording_type.slug,
        "bindings": {}, "defaults": {},
        "date_order": "DMY", "identity_columns": [],
        "grouping": "rows", "crossing_map": {}, "genotype_maps": {}, "genotype_terms": {},
        "procedure_drafts": [],
    }
    for key, _label, hints in targets(experiment):
        candidates = [c for c in columns if normalize(c["heading"]) in {normalize(h) for h in hints}]
        if len(candidates) == 1:
            definition["bindings"][key] = binding(candidates[0])
    for field in experiment.recording_type.fields.filter(active=True):
        if field.default_value is not None:
            definition["defaults"][f"field:{field.key}"] = field.default_value
    return definition


def validate_definition(definition, experiment, columns):
    """The same validation applies to manual configuration and future profiles."""
    if definition.get("version") != 2 or definition.get("recording_type") != experiment.recording_type.slug:
        raise ValidationError("This mapping version or recording type is incompatible. Remap this file.")
    procedure_definitions(definition.get("procedure_drafts", []))
    if definition.get("date_order") not in {"DMY", "MDY", "YMD"} or definition.get("grouping") not in {"rows", "date"}:
        raise ValidationError("Choose a valid date format and grouping mode.")
    valid_targets = {key for key, *_ in targets(experiment)}
    keys = set(definition.get("bindings", {})) | set(definition.get("defaults", {}))
    for key in keys:
        if key.startswith("genotype:"):
            if not MouseLine.objects.filter(pk=key.split(":")[1], active=True).exists():
                raise ValidationError("A mapped line is no longer available. Remap the genotype columns.")
            default = definition.get("defaults", {}).get(key)
            if default and not GenotypeCategory.objects.filter(code=default, active=True).exists():
                raise ValidationError("A shared genotype category is no longer available.")
        elif key not in valid_targets | {"protocol_id", "protocol_start_date", "crossing_id", "species_id", "marking_type"}:
            raise ValidationError(f"Mapped field {key!r} is no longer available.")
    for definition_binding in [*definition.get("bindings", {}).values(), *definition.get("identity_columns", [])]:
        resolve_binding(columns, definition_binding)
    for key, model in (("protocol_id", Protocol), ("crossing_id", Crossing), ("species_id", Species)):
        value = definition["defaults"].get(key)
        query = model.objects.filter(pk=value) if value else None
        if query is not None and model is not Protocol:
            query = query.filter(active=True)
        if query is not None and not query.exists():
            raise ValidationError(f"The selected {key.removesuffix('_id')} is no longer available.")
    for value in definition.get("crossing_map", {}).values():
        if value and not Crossing.objects.filter(pk=value, active=True).exists():
            raise ValidationError("A mapped crossing is no longer available.")
    codes = set(GenotypeCategory.objects.filter(active=True).values_list("code", flat=True))
    for value_map in definition.get("genotype_maps", {}).values():
        if any(code not in codes for code in value_map.values()):
            raise ValidationError("A mapped genotype category is no longer available.")
    if any(code not in codes for code in definition.get("genotype_terms", {}).values()):
        raise ValidationError("A mapped genotype category is no longer available.")


def mapped_value(definition, table, row, target):
    selected = definition["bindings"].get(target)
    value = row["values"][resolve_binding(table["columns"], selected)] if selected else ""
    return value if value != "" else definition["defaults"].get(target, "")


def parse_date(value, order="DMY"):
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    pattern = {"DMY": "%d %m", "MDY": "%m %d", "YMD": "%Y %m %d"}[order]
    text = re.sub(r"[./-]", " ", text)
    for fmt in ([pattern] if order == "YMD" else [pattern + " %Y", pattern + " %y"]):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValidationError(f"Invalid date {value!r}; expected {order}.")


def typed_value(value, form_field, order):
    from django import forms

    if value == "" or value is None:
        return form_field.clean(None)
    if isinstance(form_field, forms.DateField):
        return form_field.clean(parse_date(value, order))
    if isinstance(form_field, forms.BooleanField):
        token = str(value).strip().casefold()
        if token not in {"true", "false", "yes", "no", "1", "0"}:
            raise ValidationError("Use Yes/No, True/False or 1/0.")
        return form_field.clean(token in {"true", "yes", "1"})
    if isinstance(form_field, forms.ChoiceField):
        matches = [code for code, label in form_field.choices if str(value).casefold() in {str(code).casefold(), str(label).casefold()}]
        value = matches[0] if len(matches) == 1 else value
    return form_field.clean(value)


def crossing_candidates(text, species_id=None):
    """Only exact names or an unambiguous full set of line names/aliases match."""
    query = Crossing.objects.filter(active=True).prefetch_related("lines")
    if species_id:
        query = query.filter(species_id=species_id)
    exact = [c for c in query if c.display_name.casefold() == text.strip().casefold()]
    if exact:
        return exact
    parts = [normalize(p) for p in re.split(r"\s+[xX×]\s+|×|;|\btimes\b", text) if p.strip()]
    candidates = []
    for crossing in query:
        lines = list(crossing.lines.all())
        if len(lines) != len(parts):
            continue
        matched = set()
        for part in parts:
            matches = [line.pk for line in lines if part in {normalize(line.name), *(normalize(a) for a in re.split(r"[,;\n]", line.aliases) if a.strip())}]
            if len(matches) != 1 or matches[0] in matched:
                break
            matched.add(matches[0])
        if len(matched) == len(lines):
            candidates.append(crossing)
    return candidates


def crossing_suggestion(text, species_id=None):
    """Suggest a clear match for review, without changing exact import resolution."""
    exact = crossing_candidates(text, species_id)
    if exact:
        return exact[0] if len(exact) == 1 else None
    token = normalize(text)
    if not token:
        return None
    crossings = Crossing.objects.filter(active=True).prefetch_related("lines")
    if species_id:
        crossings = crossings.filter(species_id=species_id)
    ranked = []
    for crossing in crossings:
        names = {normalize(crossing.display_name)}
        lines = list(crossing.lines.all())
        if len(lines) == 1:
            names.update(normalize(name) for name in [lines[0].name, *re.split(r"[,;\n]", lines[0].aliases)] if name.strip())
        score = max(difflib.SequenceMatcher(None, token, name).ratio() for name in names)
        ranked.append((score, crossing))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return None
    best, crossing = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0
    # Short identifiers and near ties need an explicit choice. Punctuation-only
    # differences are safe suggestions even for short names.
    if best == 1:
        return crossing if runner_up < 1 else None
    if len(token) >= 4 and best >= .85 and best - runner_up >= .1:
        return crossing
    return None


def genotype_term_key(raw):
    return str(raw).strip().casefold()


def resolve_genotype_term(definition, raw, line_id=None, categories=None):
    """Resolve exact terms consistently in the form and importer; never fuzzy-match here."""
    categories = list(GenotypeCategory.objects.filter(active=True)) if categories is None else categories
    active_codes = {category.code for category in categories}
    key = genotype_term_key(raw)
    maps = [definition.get("genotype_terms", {})]
    if line_id is not None:
        # Existing drafts may still contain the original per-line mappings.
        maps.append(definition.get("genotype_maps", {}).get(str(line_id), {}))
    for value_map in maps:
        matches = {code for term, code in value_map.items() if genotype_term_key(term) == key}
        if matches:
            return next(iter(matches)) if len(matches) == 1 and matches <= active_codes else None
    matches = {category.code for category in categories if key in {genotype_term_key(category.code), genotype_term_key(category.label)}}
    return next(iter(matches)) if len(matches) == 1 else None


def genotype_suggestion(raw):
    choices = list(GenotypeCategory.objects.filter(active=True))
    exact = [c.code for c in choices if normalize(raw) in {normalize(c.code), normalize(c.label)}]
    if len(exact) == 1:
        return exact[0]
    labels = {normalize(c.label): c.code for c in choices}
    close = difflib.get_close_matches(normalize(raw), labels, n=1, cutoff=.7)
    return labels[close[0]] if close else ""
