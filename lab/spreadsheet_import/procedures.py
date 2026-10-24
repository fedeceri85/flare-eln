"""Portable procedure definitions and a review against resolved animal protocols."""
from ..forms.procedures import validate_procedure_drafts
from ..models import Mouse, Protocol


def procedure_definitions(raw):
    return [{
        "procedure_type": entry["procedure_type"].pk,
        "date": entry["date"].isoformat() if entry["date"] else "",
        "lab_member": entry["lab_member"], "lab_book_page": entry["lab_book_page"],
    } for entry in validate_procedure_drafts(raw)]


def build_procedure_review(definition, animals, approvals):
    entries = validate_procedure_drafts(definition.get("procedure_drafts", []))
    if not entries:
        return [], []
    protocol_ids = {a["mouse_id"]: a["details"]["protocol"] for a in animals if not a["existing"]}
    protocol_ids.update(Mouse.objects.filter(pk__in=[a["mouse_id"] for a in animals if a["existing"]]).values_list("pk", "protocol_id"))
    for animal in animals:
        for change in animal["changes"]:
            if change["field"] == "protocol" and change["token"] in approvals:
                protocol_ids[animal["mouse_id"]] = change["new"]
    protocol_ids = {mouse_id: int(pk) if pk else None for mouse_id, pk in protocol_ids.items()}
    protocols = Protocol.objects.in_bulk(pk for pk in protocol_ids.values() if pk is not None)
    rows, errors, seen = [], [], set()
    for animal in animals:
        mouse_id = animal["mouse_id"]
        if mouse_id in seen:
            continue
        seen.add(mouse_id)
        protocol = protocols.get(protocol_ids[mouse_id])
        if protocol is None or not protocol.allows_regulated_procedures:
            errors.append(f"Animal {mouse_id}: its final protocol does not allow regulated procedures. Choose an allowed protocol and approve any proposed protocol change, or remove the procedures.")
            continue
        for entry in entries:
            rows.append({
                "animal_key": animal["key"], "mouse_id": mouse_id,
                "procedure_type": entry["procedure_type"].pk,
                "procedure_name": entry["procedure_type"].name,
                "date": entry["date"].isoformat() if entry["date"] else "",
                "lab_member": entry["lab_member"], "lab_book_page": entry["lab_book_page"],
            })
    return rows, errors
