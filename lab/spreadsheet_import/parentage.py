"""Resolve complete-file parent IDs and review each pair as one atomic change."""
from collections import defaultdict

from django.core.exceptions import ValidationError

from ..parentage import ROLES, ParentageContext, validate_parentage, ancestry_state


def pair_label(pair, role):
    pk, text = pair.get(f"{role}_id"), pair.get(f"{role}_external_id", "")
    return f"{pk} (linked; recorded ID: {text})" if pk else f"{text} (external text)" if text else "Unknown"


def build_parentage_review(draft, animals, approvals, context=None):
    from .engine import change
    context = context or ParentageContext()
    state = context.state
    lookup = defaultdict(set, {key: set(ids) for key, ids in context.ids.items()})
    for animal in animals:
        if not animal["generate_id"]:
            lookup[animal["mouse_id"].casefold()].add(animal["mouse_id"])
            if animal.get("source_animal_id"):
                lookup[animal["source_animal_id"].casefold()].add(animal["mouse_id"])
    updates, errors, warnings, changes, seen = {}, [], [], [], {}
    imported_ids = {animal["mouse_id"] for animal in animals}
    # New nodes must exist in the proposed graph before resolving any parents.
    for animal in animals:
        if not animal["existing"]:
            updates[animal["mouse_id"]] = {"sex": animal["details"].get("sex", ""), "date_of_birth": animal["details"].get("date_of_birth") or None}
    for animal in animals:
        pk = animal["mouse_id"]
        resolution = draft.state.get("animals", {}).get(animal["key"], {})
        animal["parents"] = []
        # Include approval-dependent birth/sex changes when checking warnings.
        for entry in animal["changes"]:
            if entry["field"] in {"sex", "date_of_birth"} and (approvals is None or entry["token"] in approvals):
                updates.setdefault(pk, {})[entry["field"]] = entry["new"]
        for role in ROLES:
            text = str(animal["details"].get(f"{role}_external_id", "")).strip()
            if not text:
                continue
            matches = lookup.get(text.casefold(), set())
            parent_id = next(iter(matches)) if len(matches) == 1 and not resolution.get("parent_text_only", {}).get(role) else None
            pair = {f"{role}_external_id": text, f"{role}_id": parent_id}
            if len(text) > 255:
                errors.append(f"Animal {pk}: {role} ID exceeds 255 characters.")
            if len(matches) > 1:
                warnings.append(f"Animal {pk}: {role} ID {text} is ambiguous; keeping external text. Select a parent later in the editor.")
            status = "Linked in this import" if parent_id in imported_ids and parent_id not in state else "Linked existing" if parent_id else "External text"
            animal["parents"].append({"role": role.title(), "text": text, "parent_id": parent_id, "status": status})
            key = (pk, role)
            if key in seen and seen[key] != pair:
                errors.append(f"Source identifiers resolving to animal {pk} disagree for {role}.")
            seen[key] = pair
            old = {field: state[pk].get(field) for field in pair} if animal["existing"] else None
            if animal["existing"] and pair != old:
                entry = change(animal["key"], f"parent:{role}", role.title(), old, pair)
                entry.update(display_old=pair_label(old, role), display_new=pair_label(pair, role))
                animal["changes"].append(entry)
                changes.append(entry)
                if approvals is None or entry["token"] in approvals:
                    updates.setdefault(pk, {}).update(pair)
            elif not animal["existing"]:
                updates.setdefault(pk, {}).update(pair)
    effective = {pk: dict(row) for pk, row in state.items()}
    for pk, fields in updates.items():
        effective.setdefault(pk, {"mouse_id": pk}).update(fields)
    # Retrospective links outside the file are resolved only through Maintenance.
    reconciliations = []  # Retain the review payload key for older draft consumers.
    try:
        warnings.extend(validate_parentage(updates, state))
    except ValidationError as exc:
        errors.extend(exc.messages)
    roots = imported_ids | set(updates)
    relevant = set(ancestry_state(roots, state)) | set(ancestry_state(roots, effective))
    return {"updates": updates, "changes": changes, "reconciliations": reconciliations,
        "warnings": list(dict.fromkeys(warnings)), "errors": list(dict.fromkeys(errors)),
        "version": 3, "state": {pk: state[pk] for pk in sorted(relevant) if pk in state}}
