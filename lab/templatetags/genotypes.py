"""Align genotype results and form fields beneath shared line headings."""

from django import template

register = template.Library()


@register.filter
def genotype_table(rows):
    columns = {}
    prepared = []
    has_legacy = any(row.get("legacy_genotype") for row in rows)
    for row in rows:
        cells = {}
        for result in row.get("genotype_results", []):
            key = result["line_id"]
            columns.setdefault(key, result["label"])
            cells[key] = {"result": result}
        for field in row.get("genotype_fields", []):
            key = field.field.widget.attrs["data-line-id"]
            columns.setdefault(key, field.label)
            cells[key] = {"field": field}
        prepared.append((row, cells))
    return {
        "columns": list(columns.values()),
        "has_legacy": has_legacy,
        "rows": [dict(row, genotype_cells=[cells.get(key) for key in columns]) for row, cells in prepared],
    }
