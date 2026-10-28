"""Workspace-wide retrospective parent linking, with explicit stale-safe approval."""
from django.core.exceptions import ValidationError

from .parentage import (
    ROLES, ParentageContext, ancestry_state, fingerprint, parentage_transaction,
    save_parent_pairs, validate_parentage,
)

SCAN_VERSION = 1


def scan_missing_family_links(context=None):
    """Read the database once and index unresolved slots; do not change any animals."""
    context = context or ParentageContext()
    entries, unmatched = [], 0
    for normalized, slots in sorted(context.unresolved.items()):
        matches = context.ids.get(normalized, set())
        if not matches:
            unmatched += len(slots)
            continue
        if len(matches) == 1:
            parent_id = next(iter(matches))
            entries.extend({**candidate, 'parent_id': parent_id, 'parent_ids': [parent_id]}
                           for candidate in context.matching_candidates(parent_id))
        else:
            for pk, role, text in slots:
                entries.append({'mouse_id': pk, 'role': role, 'text': text,
                    'key': fingerprint([pk, role]), 'parent_id': '', 'parent_ids': sorted(matches),
                    'eligible': False, 'warnings': [],
                    'reason': 'More than one Animal ID matches. Edit this animal to choose the correct parent.'})
    entries.sort(key=lambda entry: (entry['mouse_id'].casefold(), entry['mouse_id'], ROLES.index(entry['role'])))
    roots = {pk for entry in entries for pk in [entry['mouse_id'], *entry['parent_ids']]}
    signature = fingerprint([SCAN_VERSION, entries, ancestry_state(roots, context.state)])
    eligible = sum(entry['eligible'] for entry in entries)
    return {'version': SCAN_VERSION, 'entries': entries, 'fingerprint': signature,
            'counts': {'proposed': len(entries), 'eligible': eligible, 'issues': len(entries) - eligible, 'unmatched': unmatched}}


@parentage_transaction
def resolve_missing_family_links(expected, selected):
    """Recompute under the common lock; validate all selected links as one graph."""
    context = ParentageContext()
    scan = scan_missing_family_links(context)
    if scan['fingerprint'] != expected:
        raise ValidationError('Animal information changed since this scan. Scan the database again before resolving links.', code='stale')
    eligible = {entry['key']: entry for entry in scan['entries'] if entry['eligible']}
    selected = set(selected)
    if not selected or not selected <= eligible.keys():
        raise ValidationError('Select eligible links from the current scan.')
    changes = {}
    for key in selected:
        entry = eligible[key]
        changes.setdefault(entry['mouse_id'], {})[f"{entry['role']}_id"] = entry['parent_id']
    warnings = validate_parentage(changes, context.state)
    save_parent_pairs(changes)
    return {'links': len(selected), 'animals': len(changes), 'warnings': warnings}
