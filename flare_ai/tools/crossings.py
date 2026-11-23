"""Read-only listing of genetic crossings for vocabulary reference."""

from lab.forms.genetics import active_crossings_queryset


def list_crossings():
    """List all active genetic crossings in the system.

    Returns every active crossing with its display name, species and
    constituent mouse lines (in gene order). Use this as a vocabulary
    reference when transcribing handwritten notes that contain crossing
    notations, when the user asks what crossings are available, or when
    the user mentions a mouse by a short code (e.g. a gene symbol such as
    TMC1) that must be matched against a crossing or line name before
    searching. This tool never changes records.
    """
    crossings = active_crossings_queryset()

    rows = []
    for crossing in crossings:
        lines = crossing.ordered_lines()
        rows.append({
            "crossing_id": crossing.crossing_id,
            "display_name": crossing.display_name,
            "canonical_key": crossing.canonical_key,
            "species": crossing.species.name,
            "lines": [line.name for line in lines],
            "crossing_type": crossing.inferred_crossing_type,
        })

    return {"crossings": rows, "count": len(rows)}