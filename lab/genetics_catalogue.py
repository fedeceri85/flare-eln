"""Identity-preserving catalogue edits; no animal or genotype rows are rewritten."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.functions import Lower

from .models import Crossing, CrossingLine, MouseLine, Species, crossing_display_name


def checked_name(lines):
    name = crossing_display_name(lines)
    if len(name) > Crossing._meta.get_field("display_name").max_length:
        raise ValidationError("The resulting crossing name is too long (maximum 500 characters).")
    return name


@transaction.atomic
def rename_line(line, name):
    # All catalogue writes take the species lock first, including creation, so
    # a concurrent rename cannot leave newly-created crossing labels stale.
    Species.objects.select_for_update().get(pk=line.species_id)
    line = MouseLine.objects.select_for_update().get(pk=line.pk)
    name = name.strip()
    if not name or len(name) > MouseLine._meta.get_field("name").max_length:
        raise ValidationError("Enter a line name of at most 200 characters.")
    if MouseLine.objects.filter(species=line.species).annotate(folded=Lower("name")).filter(folded=name.lower()).exclude(pk=line.pk).exists():
        raise ValidationError("A line with this name already exists for this species.")
    crossings = list(Crossing.objects.filter(pk__in=line.crossings.values("pk")).order_by("pk").select_for_update())
    updates = []
    for crossing in crossings:
        lines = crossing.ordered_lines()
        for member in lines:
            if member.pk == line.pk:
                member.name = name
        updates.append((crossing, checked_name(lines)))
    # Deliberately bypass MouseLine.save's single-crossing creation hook.
    MouseLine.objects.filter(pk=line.pk).update(name=name)
    for crossing, display_name in updates:
        Crossing.objects.filter(pk=crossing.pk).update(display_name=display_name)
    line.name = name
    return line


@transaction.atomic
def reorder_crossing(crossing, line_ids):
    Species.objects.select_for_update().get(pk=crossing.species_id)
    crossing = Crossing.objects.select_for_update().get(pk=crossing.pk)
    memberships = list(crossing.crossing_lines.select_related("line").order_by("position", "pk"))
    by_line = {membership.line_id: membership for membership in memberships}
    if len(line_ids) != len(by_line) or len(set(line_ids)) != len(line_ids) or set(line_ids) != set(by_line):
        raise ValidationError("Only the order of the existing lines can change. Do not add, remove or repeat lines.")
    name = checked_name([by_line[pk].line for pk in line_ids])
    for position, pk in enumerate(line_ids):
        CrossingLine.objects.filter(pk=by_line[pk].pk).update(position=position)
    Crossing.objects.filter(pk=crossing.pk).update(display_name=name)
    return crossing
