"""Parent pairs, graph validation and explicit reconciliation; no colony records."""
from collections import defaultdict, OrderedDict
from functools import wraps
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import logging
from time import perf_counter

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction

from .models import Mouse

logger = logging.getLogger(__name__)

ROLES = ("mother", "father")
GRAPH_FIELDS = ("mouse_id", "mother_id", "father_id", "mother_external_id", "father_external_id", "sex", "date_of_birth")


_model_context = ContextVar("parentage_model_context", default=None)


@contextmanager
def model_parentage_context(context):
    """Private service scope: per-object checks share the batch's baseline ancestry."""
    token = _model_context.set(context)
    try:
        yield
    finally:
        _model_context.reset(token)


def parentage_review(fn):
    @wraps(fn)
    def wrapped(*args, parentage_context=None, **kwargs):
        context = parentage_context or ParentageContext()
        with model_parentage_context(context):
            return fn(*args, parentage_context=context, **kwargs)
    return wrapped


def validate_mouse_parentage(mouse):
    fields = {"mother_id": mouse.mother_id, "father_id": mouse.father_id, "sex": mouse.sex, "date_of_birth": mouse.date_of_birth}
    context = _model_context.get()
    if context is None:
        return validate_parentage({mouse.pk: fields})
    parents = [mouse.mother_id, mouse.father_id]
    if mouse.pk in parents:
        raise ValidationError(f"Animal {mouse.pk} cannot be its own parent.")
    if parents[0] and parents[0] == parents[1]:
        raise ValidationError(f"Animal {mouse.pk} cannot have the same linked mother and father.")
    baseline = context.state.get(mouse.pk)
    if baseline and all(baseline.get(f"{role}_id") == parent for role, parent in zip(ROLES, parents)):
        return []  # Batch service validates the final graph; these edges are unchanged.
    for role, parent in zip(ROLES, parents):
        if parent and parent not in context.state:
            raise ValidationError(f"Animal {mouse.pk}: selected {role} {parent} no longer exists.")
        if parent and mouse.pk in context.ancestors(parent):
            raise ValidationError(f"Parentage would create an ancestry cycle involving {mouse.pk}.")
    return []


def parentage_lock():
    """Acquire before other application locks; transaction lifetime, shared by all writers."""
    if connection.vendor == "postgresql":
        started = perf_counter()
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [0x464C415245])
        logger.debug("Parentage lock wait_ms=%.3f", (perf_counter() - started) * 1000)


def parentage_transaction(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with transaction.atomic():
            parentage_lock()
            started = perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                logger.debug("Parentage operation=%s work_ms=%.3f", fn.__name__, (perf_counter() - started) * 1000)
    return wrapped


def graph_state():
    return {row["mouse_id"]: row for row in Mouse.objects.order_by("pk").values(*GRAPH_FIELDS)}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, cls=DjangoJSONEncoder).encode()).hexdigest()


def ancestry_state(roots, state):
    """Only related ancestry should invalidate a review; unrelated animals may change."""
    seen, pending = set(), list(roots)
    while pending:
        pk = pending.pop()
        if pk in seen or pk not in state:
            continue
        seen.add(pk)
        pending.extend(state[pk].get(f"{role}_id") for role in ROLES if state[pk].get(f"{role}_id"))
    return {pk: state[pk] for pk in sorted(seen)}


def reconciliation_fingerprint(parent_id, candidates, state):
    roots = {parent_id, *(candidate["mouse_id"] for candidate in candidates)}
    return fingerprint([2, candidates, ancestry_state(roots, state)])


def validate_parentage(changes, state=None):
    """Validate the FINAL graph, including unsaved nodes. Return nonblocking warnings."""
    graph = {pk: dict(row) for pk, row in (state if state is not None else graph_state()).items()}
    for pk, fields in changes.items():
        graph.setdefault(pk, {"mouse_id": pk}).update(fields)
    warnings = []
    for pk, row in graph.items():
        parents = [row.get(f"{role}_id") for role in ROLES]
        if pk in parents:
            raise ValidationError(f"Animal {pk} cannot be its own parent.")
        if parents[0] and parents[0] == parents[1]:
            raise ValidationError(f"Animal {pk} cannot have the same linked mother and father.")
        for role, parent_id in zip(ROLES, parents):
            if not parent_id:
                continue
            if parent_id not in graph:
                raise ValidationError(f"Animal {pk}: selected {role} {parent_id} no longer exists.")
            if pk not in changes:
                continue
            parent = graph[parent_id]
            if parent.get("sex") and parent["sex"] != ("F" if role == "mother" else "M"):
                warnings.append(f"Animal {pk}: {role} {parent_id} has a conflicting recorded sex.")
            born, parent_born = row.get("date_of_birth"), parent.get("date_of_birth")
            if born and parent_born and str(parent_born) >= str(born):
                warnings.append(f"Animal {pk}: {role} {parent_id} was born on or after the offspring.")
    # Iterative DFS avoids recursion limits for long lineages.
    done, active = set(), set()
    for start in graph:
        stack = [(start, False)]
        while stack:
            pk, leaving = stack.pop()
            if leaving:
                active.discard(pk)
                done.add(pk)
                continue
            if pk in done:
                continue
            if pk in active:
                raise ValidationError(f"Parentage would create an ancestry cycle involving {pk}.")
            active.add(pk)
            stack.append((pk, True))
            stack.extend((graph[pk][f"{role}_id"], False) for role in ROLES if graph[pk].get(f"{role}_id"))
    return list(dict.fromkeys(warnings))


@parentage_transaction
def apply_parentage(changes):
    if not changes:
        return []
    state = graph_state()
    updates = {pk: dict(fields) for pk, fields in changes.items()}
    for pk, fields in updates.items():
        for role in ROLES:
            if fields.get(f"{role}_id") and not fields.get(f"{role}_external_id", state.get(pk, {}).get(f"{role}_external_id")):
                fields[f"{role}_external_id"] = fields[f"{role}_id"]
    warnings = validate_parentage(updates, state)
    save_parent_pairs(updates)
    return warnings


def save_parent_pairs(updates):
    """Internal persistence step; callers validate the complete batch under the lock."""
    groups = defaultdict(list)
    allowed = {f"{role}{suffix}" for role in ROLES for suffix in ("_id", "_external_id")}
    for pk, fields in updates.items():
        pairs = {key: value for key, value in fields.items() if key in allowed}
        if pairs:
            groups[tuple(sorted(pairs))].append(Mouse(mouse_id=pk, **pairs))
    for fields, mice in groups.items():
        names = [Mouse._meta.get_field(field).name for field in fields]
        if Mouse.objects.bulk_update(mice, names, batch_size=200) != len(mice):
            raise ValidationError("One or more animals no longer exist. Refresh before linking.")


class ParentageContext:
    """Operation-local snapshot and indexes; never cached across requests or commits."""
    def __init__(self, state=None):
        self.state = graph_state() if state is None else state
        self.ids = defaultdict(set)
        self.unresolved = defaultdict(list)
        self._ancestors = OrderedDict()
        self._ancestor_nodes = 0
        for pk, row in self.state.items():
            self.ids[pk.casefold()].add(pk)
            for role in ROLES:
                text = row.get(f"{role}_external_id", "")
                if not row.get(f"{role}_id") and text.strip():
                    self.unresolved[text.strip().casefold()].append((pk, role, text))

    def has_candidates(self, parent_id):
        return bool(self.unresolved.get(parent_id.casefold()))

    def ancestors(self, pk):
        if pk not in self._ancestors:
            ancestors, pending = set(), [pk]
            while pending:
                current = pending.pop()
                if current in ancestors or current not in self.state:
                    continue
                ancestors.add(current)
                pending.extend(self.state[current].get(f"{role}_id") for role in ROLES if self.state[current].get(f"{role}_id"))
            while self._ancestors and self._ancestor_nodes + len(ancestors) > 100_000:
                _, evicted = self._ancestors.popitem(last=False)
                self._ancestor_nodes -= len(evicted)
            self._ancestors[pk] = ancestors
            self._ancestor_nodes += len(ancestors)
        self._ancestors.move_to_end(pk)
        return self._ancestors[pk]

    def matching_candidates(self, parent_id):
        candidates, accepted = [], set()
        matches = self.ids.get(parent_id.casefold(), set())
        parent = self.state.get(parent_id, {})
        ancestors = self.ancestors(parent_id) if self.has_candidates(parent_id) else set()
        for pk, role, text in sorted(self.unresolved.get(parent_id.casefold(), ()), key=lambda slot: (slot[0], ROLES.index(slot[1]))):
            row = self.state[pk]
            other = "father" if role == "mother" else "mother"
            entry = {"mouse_id": pk, "role": role, "text": text, "key": fingerprint([pk, role]), "eligible": True, "warnings": []}
            reason = ""
            if matches != {parent_id}:
                reason = "Multiple Animal IDs match; select the parent explicitly in the animal editor."
            elif pk == parent_id:
                reason = f"Animal {pk} cannot be its own parent."
            elif row.get(f"{other}_id") == parent_id or pk in accepted:
                reason = f"Animal {pk} cannot have the same linked mother and father."
            elif pk in ancestors:
                reason = f"Parentage would create an ancestry cycle involving {pk}."
            if reason:
                entry.update(eligible=False, reason=reason)
            else:
                accepted.add(pk)
                if parent.get("sex") and parent["sex"] != ("F" if role == "mother" else "M"):
                    entry["warnings"].append(f"Animal {pk}: {role} {parent_id} has a conflicting recorded sex.")
                born, parent_born = row.get("date_of_birth"), parent.get("date_of_birth")
                if born and parent_born and str(parent_born) >= str(born):
                    entry["warnings"].append(f"Animal {pk}: {role} {parent_id} was born on or after the offspring.")
            candidates.append(entry)
        return candidates


def matching_candidates(parent_id, state=None):
    return ParentageContext(state).matching_candidates(parent_id)


@parentage_transaction
def reconcile_many(selections):
    """Check all reviews against one snapshot, then apply every selected link together."""
    context = ParentageContext()
    state = context.state
    changes = defaultdict(dict)
    for parent_id, selection in selections.items():
        if parent_id not in state:
            raise ValidationError("The parent no longer exists.")
        candidates = context.matching_candidates(parent_id)
        if reconciliation_fingerprint(parent_id, candidates, state) != selection["expected"]:
            raise ValidationError("Matching animals changed. Review the refreshed list before linking.")
        eligible = {entry["key"]: entry for entry in candidates if entry["eligible"]}
        if not selection["selected"] or not set(selection["selected"]) <= eligible.keys():
            raise ValidationError("Select eligible offspring from the current list.")
        for key in selection["selected"]:
            entry = eligible[key]
            fields = changes[entry["mouse_id"]]
            field = f"{entry['role']}_id"
            if field in fields and fields[field] != parent_id:
                raise ValidationError("Two selected parents match the same offspring slot. Select one parent.")
            fields[field] = parent_id
    warnings = validate_parentage(changes, state)
    save_parent_pairs(changes)
    return warnings


def reconcile(parent_id, selected, expected):
    return reconcile_many({parent_id: {"selected": selected, "expected": expected}})
