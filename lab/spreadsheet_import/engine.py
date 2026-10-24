"""Build a review without writes; commit exactly that review atomically."""
import hashlib
import json
from collections import OrderedDict
from datetime import date

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from ..forms.genetics import sync_mouse_structured_genetics
from ..forms.mice import MouseEditForm, MouseForm
from ..forms.recordings import RecordingForm, parse_recording_paths
from ..forms.procedures import ProcedureCreateForm
from ..models import (
    Crossing, Experiment, GenotypeCategory, Mouse, MouseGenotype, MouseLine,
    ProcedureType, Protocol, Recording, RecordingField, RecordingType, Species, SpreadsheetImportDraft,
)
from ..utils import generate_mouse_id
from .mapping import ANIMAL_FIELDS, crossing_candidates, genotype_term_key, mapped_value, parse_date, resolve_genotype_term, typed_value, validate_definition
from .source import resolve_binding, table_from_sheet
from .procedures import build_procedure_review


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, cls=DjangoJSONEncoder).encode()).hexdigest()


def identity_signature(definition):
    keys = ("animal:identifier", "animal:tattoo")
    return digest([
        [definition.get("bindings", {}).get(key), definition.get("defaults", {}).get(key, "")]
        for key in keys
    ] + [definition.get("identity_columns", [])])


def draft_table(draft):
    sheet = next((s for s in draft.source if s["name"] == draft.state.get("sheet")), None)
    if sheet is None:
        raise ValidationError("Choose a worksheet first.")
    return table_from_sheet(sheet, draft.state.get("header_row", 1))


def selected_rows(draft, table):
    numbers = set(draft.state.get("selected_rows", []))
    rows = [r for r in table["rows"] if r["number"] in numbers]
    if not rows:
        raise ValidationError("Select at least one data row.")
    if len(rows) != len(numbers):
        raise ValidationError("Selected rows changed. Select the rows again.")
    return rows


def mapped_label(definition, table, target, label):
    selected = definition["bindings"].get(target)
    if selected:
        column = table["columns"][resolve_binding(table["columns"], selected)]
        return f"{label} ({column['label']})"
    return label


def animal_groups(draft, table=None):
    table = table or draft_table(draft)
    definition = draft.configuration
    groups = OrderedDict()
    for row in selected_rows(draft, table):
        animal_id = str(mapped_value(definition, table, row, "animal:identifier")).strip()
        local_id = str(mapped_value(definition, table, row, "animal:tattoo")).strip()
        extra = [] if animal_id else [str(row["values"][resolve_binding(table["columns"], b)]).strip() for b in definition.get("identity_columns", [])]
        identifier = animal_id or local_id
        # Animal IDs always name one animal. Additional distinguishing columns
        # apply only to reused local markings; missing identifiers never merge.
        identity = [animal_id.casefold()] if animal_id else ["local", local_id.casefold(), *extra] if local_id else ["missing", row["number"]]
        key = digest(identity)[:20]
        group = groups.setdefault(key, {
            "key": key, "identifier": identifier, "animal_id": animal_id,
            "identifier_kind": "official" if animal_id else "local", "extra": extra, "rows": [],
        })
        group["rows"].append(row)
    return list(groups.values())


def initial_resolution(group, definition):
    identifier = group["identifier"]
    official = bool(group["animal_id"])
    query = Mouse.objects.filter(mouse_id__iexact=identifier) if official else Mouse.objects.none()
    matches = list(query[:2]) if identifier else []
    if len(matches) == 1:
        return {"mode": "link", "mouse_id": matches[0].pk, "details": {}}
    return {"mode": "create", "mouse_id": identifier if official else "", "generate_id": not official, "details": {}}


def consistent_value(draft, table, rows, target, convert=None):
    values = []
    for row in rows:
        value = mapped_value(draft.configuration, table, row, target)
        if value != "" and convert:
            value = convert(value)
        if value != "" and value not in values:
            values.append(value)
    if len(values) > 1:
        raise ValidationError(f"Rows {', '.join(str(r['number']) for r in rows)} disagree for {target}. Correct the spreadsheet or revise the selection/mapping.")
    return values[0] if values else ""


def group_details(draft, table, group):
    definition = draft.configuration
    details = {}
    overrides = draft.state.get("animals", {}).get(group["key"], {}).get("details", {})
    for key, _label, _hints in ANIMAL_FIELDS:
        if key == "identifier":
            continue
        if key == "crossing" and overrides.get("crossing_definition"):
            details[key] = ""
        elif overrides.get(key):
            details[key] = overrides[key]
        else:
            convert = None
            if key == "date_of_birth":
                convert = lambda value: parse_date(value, definition["date_order"]).isoformat()
            elif key == "sex":
                convert = lambda value: {"m": "M", "male": "M", "f": "F", "female": "F"}.get(str(value).casefold(), value)
            elif key == "status":
                convert = lambda value: {s.casefold(): s for s, _ in Mouse.STATUS_CHOICES}.get(str(value).casefold(), value)
            details[key] = consistent_value(draft, table, group["rows"], f"animal:{key}", convert=convert)
    if details["date_of_birth"]:
        details["date_of_birth"] = parse_date(details["date_of_birth"], definition["date_order"]).isoformat()
    if details["sex"]:
        sex = str(details["sex"]).casefold()
        if sex not in {"m", "male", "f", "female"}:
            raise ValidationError(f"Animal {group['identifier']}: unrecognised sex {details['sex']!r}.")
        details["sex"] = "M" if sex in {"m", "male"} else "F"
    if details["status"]:
        states = {s.casefold(): s for s, _ in Mouse.STATUS_CHOICES}
        if str(details["status"]).casefold() not in states:
            raise ValidationError(f"Animal {group['identifier']}: choose Alive or Culled.")
        details["status"] = states[str(details["status"]).casefold()]
    raw_crossing = str(details.pop("crossing")).strip()
    crossing_id = definition["defaults"].get("crossing_id")
    if not crossing_id and raw_crossing:
        crossing_id = definition.get("crossing_map", {}).get(raw_crossing)
        if not crossing_id:
            candidates = crossing_candidates(raw_crossing, definition["defaults"].get("species_id"))
            if len(candidates) == 1:
                crossing_id = candidates[0].pk
        individual = draft.state.get("animals", {}).get(group["key"], {}).get("details", {}).get("crossing_definition")
        if not crossing_id and not individual:
            raise ValidationError(f"Map strain description {raw_crossing!r} to an existing crossing in Crossings & genotypes.")
    details["crossing_definition"] = crossing_id or ""
    details["protocol"] = definition["defaults"].get("protocol_id", "")
    return details


def genotype_values(draft, table, group, crossing):
    calls = {}
    if not crossing:
        return calls
    categories = list(GenotypeCategory.objects.filter(active=True))
    active_codes = {category.code for category in categories}
    for line in crossing.ordered_lines():
        target = f"genotype:{line.pk}"
        override = draft.state.get("animals", {}).get(group["key"], {}).get("genotypes", {}).get(str(line.pk))
        if override:
            if override not in active_codes:
                raise ValidationError("An individual genotype category is no longer available.")
            calls[str(line.pk)] = override
            continue
        codes = set()
        for row in group["rows"]:
            value = mapped_value(draft.configuration, table, row, target)
            if not genotype_term_key(value):
                continue
            code = resolve_genotype_term(draft.configuration, value, line.pk, categories=categories)
            if not code:
                raise ValidationError(f"Animal {group['identifier']}, {line.name}: map genotype value {value!r} to an existing category.")
            codes.add(code)
        if len(codes) > 1:
            raise ValidationError(f"Animal {group['identifier']}, {line.name}: conflicting genotype results in rows {[r['number'] for r in group['rows']]}. Correct the spreadsheet.")
        if codes:
            calls[str(line.pk)] = codes.pop()
    return calls


def change(group_key, field, label, old, new):
    return {"token": f"{group_key}:{field}", "field": field, "label": label, "old": old, "new": new}


def animal_display_details(details, calls=None):
    result = []
    for key, value in details.items():
        if value == "" or key == "marking_type":
            continue
        field = Mouse._meta.get_field(key)
        label = {"crossing_definition": "Crossing", "marking_type": "Local ID type"}.get(key, field.verbose_name.title())
        if key in {"protocol", "crossing_definition"}:
            model = Protocol if key == "protocol" else Crossing
            obj = model.objects.filter(pk=value).first()
            value = str(obj) if obj else "Unavailable"
        elif field.choices:
            value = dict(field.choices).get(value, value)
        result.append((label, value))
    labels = dict(GenotypeCategory.objects.values_list("code", "label"))
    for pk, code in (calls or {}).items():
        line = MouseLine.objects.get(pk=pk)
        result.append((f"Genotype: {line.name}", labels.get(code, code)))
    return result


def environmental_state(experiment, mouse_ids):
    """Include all referenced schemas/catalogues, not only field values in the file."""
    return {
        "experiment": [experiment.pk, experiment.recording_type_id],
        "type": list(RecordingType.objects.filter(pk=experiment.recording_type_id).values()),
        "fields": list(RecordingField.objects.filter(recording_type_id=experiment.recording_type_id).order_by("pk").values()),
        "mice": list(Mouse.objects.filter(pk__in=mouse_ids).order_by("pk").values()),
        "calls": list(MouseGenotype.objects.filter(mouse_id__in=mouse_ids).order_by("pk").values()),
        "crossings": list(Crossing.objects.order_by("pk").values()),
        "members": list(Crossing.lines.through.objects.order_by("pk").values()),
        "lines": list(MouseLine.objects.order_by("pk").values()),
        "categories": list(GenotypeCategory.objects.order_by("pk").values()),
        "protocols": list(Protocol.objects.order_by("pk").values()),
        "procedure_types": list(ProcedureType.objects.order_by("pk").values()),
        "species": list(Species.objects.order_by("pk").values()),
    }


def build_review(draft, approvals=None):
    """Return a deterministic, JSON-safe preview without changing database rows."""
    experiment = draft.experiment
    table = draft_table(draft)
    definition = draft.configuration
    validate_definition(definition, experiment, table["columns"])
    errors, animals, all_changes = [], [], []
    rows = selected_rows(draft, table)
    reserved_ids = set()
    reserved_identities = set()
    for group in animal_groups(draft, table):
        try:
            if not group["identifier"]:
                raise ValidationError("Supply an Animal ID or Local identifier for every selected row in Recording fields.")
            resolution = draft.state.get("animals", {}).get(group["key"])
            if not resolution:
                raise ValidationError(f"Resolve animal {group['identifier'] or '(missing identifier)'} in the Animals tab.")
            details = group_details(draft, table, group)
            details.update({k: v for k, v in resolution.get("details", {}).items() if v != "" and k != "marking_type"})
            if details.get("protocol") and not Protocol.objects.filter(pk=details["protocol"]).exists():
                raise ValidationError("The animal's selected protocol is no longer available.")
            mouse = None
            if resolution["mode"] == "link":
                mouse = Mouse.objects.filter(pk=resolution.get("mouse_id")).first()
                if mouse is None:
                    raise ValidationError("The selected animal no longer exists.")
                # Species is always validated against the final crossing, not the identifier text.
            crossing_id = details.get("crossing_definition") or (mouse.crossing_definition_id if mouse else None)
            crossing = Crossing.objects.filter(pk=crossing_id, active=True).first() if crossing_id else None
            if crossing_id and crossing is None:
                raise ValidationError("Choose an available crossing.")
            if not mouse and crossing is None:
                raise ValidationError("Select an existing crossing for this new animal.")
            species_id = definition["defaults"].get("species_id")
            if crossing and species_id and crossing.species_id != int(species_id):
                raise ValidationError("The animal crossing does not belong to the selected species.")
            calls = genotype_values(draft, table, group, crossing)
            changes = []
            if mouse:
                for field, value in details.items():
                    if value == "":
                        continue
                    old = getattr(mouse, field + "_id" if field in {"protocol", "crossing_definition"} else field)
                    old = old.isoformat() if isinstance(old, date) else old
                    if str(old) != str(value):
                        label = "Crossing" if field == "crossing_definition" else Mouse._meta.get_field(field).verbose_name.title()
                        display_old, display_new = old, value
                        if field in {"protocol", "crossing_definition"}:
                            model = Protocol if field == "protocol" else Crossing
                            display_old = str(model.objects.get(pk=old)) if old else ""
                            display_new = str(model.objects.get(pk=value))
                        entry = change(group["key"], field, label, old, value)
                        entry.update(display_old=display_old, display_new=display_new)
                        changes.append(entry)
                existing = dict(mouse.genotype_calls.values_list("mouse_line_id", "zygosity"))
                for pk, code in calls.items():
                    if existing.get(int(pk), "unknown") != code:
                        entry = change(group["key"], f"genotype:{pk}", MouseLine.objects.get(pk=pk).name, existing.get(int(pk), "unknown"), code)
                        labels = dict(GenotypeCategory.objects.values_list("code", "label"))
                        entry.update(display_old=labels.get(entry["old"], entry["old"]), display_new=labels.get(code, code))
                        changes.append(entry)
                animal_id = mouse.pk
            else:
                details["status"] = details.get("status") or "Alive"
                details["marking_type"] = "other"
                values = {k: v for k, v in details.items() if v != ""}
                generate = bool(resolution.get("generate_id"))
                if generate and group["animal_id"]:
                    raise ValidationError("An Animal ID was supplied. Use that ID, or remove its mapping before generating an ID.")
                form = MouseForm(data={**values, "mouse_id": resolution.get("mouse_id", ""), "generate_mouse_id": generate})
                if not form.is_valid():
                    raise ValidationError([f"{field}: {', '.join(messages)}" for field, messages in form.errors.items()])
                if generate:
                    cleaned = form.cleaned_data
                    identity = (str(cleaned["date_of_birth"]), cleaned["breeding_pair"], crossing.pk, cleaned["tattoo"], cleaned["marking_type"])
                    if identity in reserved_identities:
                        raise ValidationError("Two new animals have the same birth date, breeding pair, crossing and local ID. Resolve them as one animal or correct their details.")
                    reserved_identities.add(identity)
                    animal_id = generate_mouse_id(cleaned["date_of_birth"], cleaned["breeding_pair"], crossing.display_name, cleaned["tattoo"])
                    base, number = animal_id, 1
                    while animal_id in reserved_ids or Mouse.objects.filter(pk=animal_id).exists():
                        animal_id = f"{base}_{number}"
                        number += 1
                else:
                    animal_id = form.cleaned_data["mouse_id"]
                if len(animal_id) > Mouse._meta.get_field("mouse_id").max_length:
                    raise ValidationError("The generated Animal ID exceeds 100 characters. Supply a shorter official Animal ID or shorten its component details.")
                if animal_id.casefold() in {pk.casefold() for pk in reserved_ids} or Mouse.objects.filter(pk__iexact=animal_id).exists():
                    raise ValidationError(f"Animal ID {animal_id} is already in use.")
                reserved_ids.add(animal_id)
            animal = {"key": group["key"], "identifier": group["identifier"], "rows": [r["number"] for r in group["rows"]],
                      "existing": bool(mouse), "mouse_id": animal_id, "details": details, "calls": calls,
                      "crossing_id": crossing.pk if crossing else None, "changes": changes,
                      "generate_id": not mouse and bool(resolution.get("generate_id")), "display_details": animal_display_details(details, calls)}
            animals.append(animal)
            all_changes.extend(changes)
        except (ValidationError, ValueError, TypeError) as exc:
            messages = exc.messages if isinstance(exc, ValidationError) else [str(exc)]
            errors.extend(f"Rows {', '.join(str(r['number']) for r in group['rows'])}: {m}" for m in messages)

    # Review approvals affect the final crossing and its valid genotype calls.
    approved = set(approvals) if approvals is not None else {c["token"] for c in all_changes}
    for animal in animals:
        if animal["existing"]:
            current = Mouse.objects.get(pk=animal["mouse_id"])
            crossing_change = next((c for c in animal["changes"] if c["field"] == "crossing_definition"), None)
            if crossing_change and crossing_change["token"] not in approved:
                animal["crossing_id"] = current.crossing_definition_id
                allowed = set(current.crossing_definition.lines.values_list("pk", flat=True)) if current.crossing_definition_id else set()
                invalid = [pk for pk in animal["calls"] if int(pk) not in allowed and f"{animal['key']}:genotype:{pk}" in approved]
                if invalid:
                    errors.append(f"Animal {animal['mouse_id']}: approve its crossing change before applying genotype calls for that crossing.")
            # Apply the same edit validation as the normal animal editor before writes.
            data = {field: getattr(current, field + "_id" if field in {"protocol", "crossing_definition"} else field)
                    for field in MouseEditForm.Meta.fields}
            for entry in animal["changes"]:
                if entry["token"] in approved and not entry["field"].startswith("genotype:"):
                    data[entry["field"]] = entry["new"]
            edit_form = MouseEditForm(data=data, instance=current)
            if not edit_form.is_valid():
                errors.extend(f"Animal {animal['mouse_id']}, {field}: {', '.join(messages)}" for field, messages in edit_form.errors.items())

    proposed = {}
    for animal in animals:
        if not animal["existing"]:
            continue
        for entry in animal["changes"]:
            key = (animal["mouse_id"], entry["field"])
            if key in proposed and proposed[key] != entry["new"]:
                errors.append(f"Source identifiers resolving to animal {animal['mouse_id']} disagree for {entry['label']}. Correct the spreadsheet or animal resolutions.")
            proposed[key] = entry["new"]

    records, grouped = [], OrderedDict()
    form = RecordingForm(experiment=experiment)
    by_row = {n: a for a in animals for n in a["rows"]}
    for row in rows:
        current_label = "Recording"
        try:
            values = {}
            for target in ("recording_date", "notes"):
                current_label = mapped_label(definition, table, target, form.fields[target].label)
                raw = mapped_value(definition, table, row, target)
                # Empty scalar cells stay absent until groups are merged.
                if raw != "":
                    value = typed_value(raw, form.fields[target], definition["date_order"])
                    values[target] = value.isoformat() if isinstance(value, date) else value
            if not values.get("recording_date"):
                raise ValidationError("Recording date is required; map a column or enter a shared date.")
            dynamic = {}
            for field_name, field in form.dynamic_fields:
                if form.is_genotyping_recording() and field.key == "crossing":
                    continue
                current_label = mapped_label(definition, table, f"field:{field.key}", field.label)
                raw = mapped_value(definition, table, row, f"field:{field.key}")
                if raw != "":
                    value = typed_value(raw, form.fields[field_name], definition["date_order"])
                    dynamic[field.key] = value.isoformat() if isinstance(value, date) else value
            current_label = mapped_label(definition, table, "data_paths", "Recording paths")
            paths = parse_recording_paths(str(mapped_value(definition, table, row, "data_paths")))
            key = values["recording_date"] if definition["grouping"] == "date" else str(row["number"])
            record = grouped.setdefault(key, {"rows": [], "scalars": {}, "values": {}, "paths": [], "animal_keys": []})
            for field, value in values.items():
                if field in record["scalars"] and record["scalars"][field] != value:
                    label = mapped_label(definition, table, field, form.fields[field].label)
                    current_label = label
                    raise ValidationError(f"Conflicting values in rows {record['rows'] + [row['number']]}; correct the spreadsheet or revise the mapping/selection.")
                record["scalars"][field] = value
            for field, value in dynamic.items():
                if field in record["values"] and record["values"][field] != value:
                    label = next(f.label for _, f in form.dynamic_fields if f.key == field)
                    label = mapped_label(definition, table, f"field:{field}", label)
                    current_label = label
                    raise ValidationError(f"Conflicting values in rows {record['rows'] + [row['number']]}; correct the spreadsheet or revise the mapping/selection.")
                record["values"][field] = value
            record["rows"].append(row["number"])
            record["paths"] = list(dict.fromkeys([*record["paths"], *paths]))
            if row["number"] in by_row:
                record["animal_keys"] = list(dict.fromkeys([*record["animal_keys"], by_row[row["number"]]["key"]]))
        except ValidationError as exc:
            errors.extend(f"Row {row['number']}, {current_label}: {m}" for m in exc.messages)
    by_key = {a["key"]: a for a in animals}
    for record in grouped.values():
        try:
            mice = [by_key[k] for k in record["animal_keys"]]
            if form.is_genotyping_recording():
                crossings = {a["crossing_id"] for a in mice}
                if len(crossings) != 1 or None in crossings:
                    raise ValidationError("Genotyping recordings require animals from one crossing.")
                if any(field.key == "crossing" for _, field in form.dynamic_fields):
                    record["values"]["crossing"] = Crossing.objects.get(pk=next(iter(crossings))).display_name
            candidate = Recording(experiment=experiment, recording_type=experiment.recording_type,
                                  recording_date=record["scalars"]["recording_date"],
                                  notes=record["scalars"].get("notes", ""), data_paths=record["paths"], values=record["values"])
            candidate.full_clean()
            record["mouse_ids"] = [a["mouse_id"] for a in mice]
            record["display_values"] = [(field.label, record["values"][field.key]) for _, field in form.dynamic_fields if field.key in record["values"]]
            records.append(record)
        except ValidationError as exc:
            errors.extend(f"Rows {record['rows']}: {m}" for m in exc.messages)
    procedures, procedure_errors = build_procedure_review(definition, animals, approved)
    errors.extend(procedure_errors)
    payload = {"animals": animals, "records": records, "procedures": procedures, "changes": all_changes, "errors": errors}
    environment = environmental_state(experiment, [a["mouse_id"] for a in animals if a["existing"]])
    payload["fingerprint"] = digest({"preview": payload, "environment": environment, "configuration": definition})
    return payload


@transaction.atomic
def commit_import(draft_id, owner, experiment, approvals):
    draft = SpreadsheetImportDraft.objects.select_for_update().get(pk=draft_id, owner=owner, experiment=experiment)
    if draft.completed_at:
        return draft.state.get("result", {})
    if draft.expires_at <= timezone.now():
        raise ValidationError("This import has expired. Upload the spreadsheet again.")
    # Lock the schemas and catalogue in the same species-first order as catalogue edits.
    list(Species.objects.select_for_update().order_by("pk"))
    for model in (Crossing, MouseLine, Crossing.lines.through, GenotypeCategory, Protocol):
        list(model.objects.select_for_update().order_by("pk"))
    experiment = Experiment.objects.select_for_update().get(pk=experiment.pk)
    draft.experiment = experiment
    if not experiment.recording_type_id:
        raise ValidationError("The experiment recording type changed. Select a recording type and refresh the import.")
    RecordingType.objects.select_for_update().get(pk=experiment.recording_type_id)
    list(RecordingField.objects.select_for_update().filter(recording_type_id=experiment.recording_type_id).order_by("pk"))
    ids = [r.get("mouse_id") for r in draft.state.get("animals", {}).values() if r.get("mode") == "link"]
    list(Mouse.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
    list(MouseGenotype.objects.select_for_update().filter(mouse_id__in=ids).order_by("pk"))
    list(ProcedureType.objects.select_for_update().order_by("pk"))
    baseline = build_review(draft)
    if baseline["errors"]:
        raise ValidationError(baseline["errors"])
    if baseline["fingerprint"] != draft.state.get("review_fingerprint"):
        raise ValidationError("Animal data or recording configuration changed after review. Refresh the review before importing.")
    known = {c["token"] for c in baseline["changes"]}
    if not set(approvals) <= known:
        raise ValidationError("The approved changes no longer match this review. Refresh the review.")
    preview = build_review(draft, approvals=approvals)
    if preview["errors"]:
        raise ValidationError(preview["errors"])
    saved, created, updated = {}, 0, 0
    for animal in preview["animals"]:
        if animal["existing"]:
            mouse = Mouse.objects.get(pk=animal["mouse_id"])
            fields = []
            calls = {}
            crossing = mouse.crossing_definition
            for entry in animal["changes"]:
                if entry["token"] not in approvals:
                    continue
                field, value = entry["field"], entry["new"]
                if field.startswith("genotype:"):
                    calls[int(field.split(":")[1])] = value
                elif field == "crossing_definition":
                    crossing = Crossing.objects.get(pk=value)
                else:
                    setattr(mouse, field + "_id" if field == "protocol" else field, value)
                    fields.append(field)
            mouse.full_clean(exclude=["crossing_definition"])
            if fields:
                mouse.save(update_fields=fields)
            if crossing and (calls or crossing.pk != mouse.crossing_definition_id):
                sync_mouse_structured_genetics(mouse, crossing, calls, preserve_missing=True)
            if fields or calls or any(c["field"] == "crossing_definition" and c["token"] in approvals for c in animal["changes"]):
                updated += 1
        else:
            data = animal["details"]
            kwargs = {k: v for k, v in data.items() if v != ""}
            crossing = Crossing.objects.get(pk=kwargs.pop("crossing_definition"))
            protocol_id = kwargs.pop("protocol")
            mouse = Mouse(mouse_id=animal["mouse_id"], protocol_id=protocol_id, crossing_definition=crossing,
                          added_by=owner, id_status=Mouse.ID_TEMPORARY if animal["generate_id"] else Mouse.ID_ASSIGNED, **kwargs)
            mouse.full_clean()
            mouse.save()
            sync_mouse_structured_genetics(mouse, crossing, {int(k): v for k, v in animal["calls"].items()})
            created += 1
        saved[animal["key"]] = mouse
    recording_ids = []
    for record in preview["records"]:
        data = {**record["scalars"],
                "data_paths": "\n".join(record["paths"]), "mice": "\n".join(saved[k].pk for k in record["animal_keys"])}
        data.update({f"value_{field.pk}": record["values"][field.key] for field in experiment.recording_type.fields.all() if field.key in record["values"]})
        form = RecordingForm(data=data, experiment=experiment, owner=owner)
        if not form.is_valid():
            raise ValidationError([f"{field}: {', '.join(messages)}" for field, messages in form.errors.items()])
        recording_ids.append(form.save().pk)
    procedure_ids = []
    for entry in preview["procedures"]:
        mouse = Mouse.objects.select_related("protocol").get(pk=saved[entry["animal_key"]].pk)
        form = ProcedureCreateForm(data={key: entry[key] for key in ("procedure_type", "date", "lab_member", "lab_book_page")}, mouse=mouse)
        if not form.is_valid():
            raise ValidationError([f"Animal {mouse.pk}, {field}: {', '.join(messages)}" for field, messages in form.errors.items()])
        procedure = form.save(commit=False)
        procedure.mouse = mouse
        procedure.save()
        procedure_ids.append(procedure.pk)
    result = {"recordings": len(recording_ids), "created_animals": created, "updated_animals": updated, "recording_ids": recording_ids,
              "procedures": len(procedure_ids), "procedure_ids": procedure_ids}
    draft.completed_at = timezone.now()
    draft.state = {**draft.state, "result": result}
    draft.save(update_fields=["completed_at", "state"])
    return result
