"""Give Animal ID and Local identifier fixed meanings in import definitions."""
from copy import deepcopy

from django.db import migrations


def upgrade_identifier_mappings(apps, schema_editor):
    Draft = apps.get_model("lab", "SpreadsheetImportDraft")
    alias = schema_editor.connection.alias
    for draft in Draft.objects.using(alias).filter(completed_at__isnull=True).iterator(chunk_size=1000):
        if draft.configuration.get("version") != 1:
            continue
        definition = deepcopy(draft.configuration)
        local = definition.pop("identifier_kind", "official") == "local"
        bindings, defaults = definition.setdefault("bindings", {}), definition.setdefault("defaults", {})
        if local:
            # An explicit marking mapping already took precedence in the old
            # importer. Otherwise the old generic identifier was the marking.
            mapped_local = bool(bindings.get("animal:tattoo")) or defaults.get("animal:tattoo") not in (None, "")
            for section in (bindings, defaults):
                previous = section.pop("animal:identifier", None)
                if not mapped_local and previous is not None:
                    section["animal:tattoo"] = previous
        definition["version"] = 2
        state = deepcopy(draft.state)
        if local or definition.get("identity_columns") or any(
            resolution.get("generate_id") for resolution in state.get("animals", {}).values()
        ):
            # Local keys now have their own namespace, while extra columns can
            # no longer split a globally unique Animal ID into separate animals.
            state.pop("animals", None)
        state.pop("review_fingerprint", None)
        state["identifier_mapping_updated"] = True
        Draft.objects.using(alias).filter(pk=draft.pk).update(configuration=definition, state=state)


class Migration(migrations.Migration):
    dependencies = [("lab", "0055_sequence_recording_field")]

    operations = [
        migrations.RunPython(upgrade_identifier_mappings, reverse_code=migrations.RunPython.noop),
    ]
