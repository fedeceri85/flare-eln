"""Portable template snapshots and conservative matching against a new file."""
from collections import Counter
from copy import deepcopy

from django.core.exceptions import ValidationError

from .mapping import targets
from .source import binding


def capture_mapping(experiment, columns, data):
    """Read only column controls, without requiring import-ready field values."""
    counts = Counter(column["heading"] for column in columns)
    by_index = {str(column["index"]): column for column in columns}
    bindings = {}
    for key, label, _ in targets(experiment):
        mode = data.get(f"mode_{key}", "column")
        if mode not in {"column", "shared"}:
            raise ValidationError(f"{label}: choose a valid value source.")
        if mode == "shared":
            continue
        selected = data.get(f"column_{key}", "")
        if selected == "":
            continue
        column = by_index.get(selected)
        if column is None:
            raise ValidationError(f"{label}: choose a column from this spreadsheet.")
        saved = {**binding(column), "count": counts[column["heading"]]}
        if not column["heading"]:
            saved["index"] = column["index"]
        bindings[key] = saved
    if not bindings:
        raise ValidationError("Select at least one source column before saving a template.")
    return {"version": 1, "bindings": bindings}


def match_mapping(template, experiment, columns):
    """Return draft-compatible bindings plus warnings; never guess at gaps."""
    if template.recording_type_id != experiment.recording_type_id:
        raise ValidationError("Choose a template for this experiment type.")
    payload = template.mapping
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("bindings"), dict):
        raise ValidationError("This template version is unsupported. Save a new template.")
    labels = {key: label for key, label, _ in targets(experiment)}
    counts = Counter(column["heading"] for column in columns)
    resolved, warnings = {}, []
    for key, saved in payload["bindings"].items():
        if key not in labels:
            warnings.append(f"Field {key!r} is no longer available. Its association was skipped.")
            continue
        if not isinstance(saved, dict) or not isinstance(saved.get("heading"), str):
            raise ValidationError("This template contains an invalid column association. Save a new template.")
        heading = saved["heading"]
        description = repr(heading) if heading else "an unnamed column"
        if counts[heading] == 0:
            warnings.append(f"{labels[key]}: column {description} is missing. Choose a source column.")
            continue
        if counts[heading] != saved.get("count"):
            warnings.append(f"{labels[key]}: the number of columns headed {description} changed. Choose a source column.")
            continue
        matches = [column for column in columns if (
            column["heading"] == heading
            and (column["occurrence"] == saved.get("occurrence") if heading else column["index"] == saved.get("index"))
        )]
        if len(matches) != 1:
            warnings.append(f"{labels[key]}: column {description} moved or is ambiguous. Choose a source column.")
            continue
        resolved[key] = binding(matches[0])
    return resolved, warnings


def mapped_form_data(data, experiment, columns, resolved):
    """Replace tab 3 column selections while retaining unrelated shared values."""
    result = deepcopy(data)
    by_binding = {(column["heading"], column["occurrence"]): str(column["index"]) for column in columns}
    for key, *_ in targets(experiment):
        saved = resolved.get(key)
        result[f"column_{key}"] = by_binding[(saved["heading"], saved["occurrence"])] if saved else ""
        if saved:
            result[f"mode_{key}"] = "column"
            result[f"default_{key}"] = ""
    return result
