from datetime import date
from urllib.parse import urlencode

from django.urls import reverse
from django.template.defaultfilters import linebreaksbr
from django.utils.html import format_html, format_html_join

from .models import Mouse


EMPTY_VALUE = format_html("&mdash;")
DEFAULT_TABLE_PAGE_SIZE = 25
TABLE_PAGE_SIZE_CHOICES = [25, 50, 100, 150, 200]


CROSSING_FILTER_AUTOCOMPLETE = {
    "url_name": "lab:crossing_lookup",
    "value_key": "display_name",
    "label_key": "label",
}


MOUSE_TABLE_COLUMN_SPECS = [
    {
        "key": "mouse_id",
        "label": "Mouse ID",
        "sort": "mouse_id",
        "url": lambda mouse: reverse("lab:mouse_record", args=[mouse.pk]),
        "link_class": "mouse-link",
        "cell_class": "mouse-id-cell",
    },
    {
        "key": "date_of_birth",
        "label": "DOB",
        "sort": "date_of_birth",
        "format": "date",
        "cell_class": "date-cell",
    },
    # {
    #     "key": "breeding_pair",
    #     "label": "Breeding pair",
    #     "sort": "breeding_pair",
    #     "cell_class": "compact-text-cell",
    # },
    {
        "key": "protocol_start_date",
        "label": "Protocol start",
        "sort": "protocol_start_date",
        "format": "date",
        "cell_class": "date-cell",
    },
    # {
    #     "key": "sex",
    #     "label": "Sex",
    #     "sort": "sex",
    #     "cell_class": "tiny-cell",
    # },
    {
        "key": "genetics_genotype_display",
        "label": "Genotype",
        "sort": "genotype",
        "cell_class": "compact-text-cell",
    },
    {
        "key": "crossing",
        "label": "Crossing/type",
        "sort": "crossing",
        "format": "crossing_type_summary",
        "cell_class": "compact-text-cell",
    },
    {
        "key": "status",
        "label": "Status",
        "sort": "status",
        "cell_class": "status-cell",
    },
    {   "key": "cull_date",
        "label": "Cull date",
        "sort": "cull_date",
        "format": "date",
        "cell_class": "date-cell",
     },
    {
        "key": "get_severity_display",
        "label": "Severity",
        "sort": "severity",
        "cell_class": "severity-cell",
    },
    {
        "key": "notes",
        "label": "Notes",
        "sort": "notes",
        "format": "linebreaks",
        "cell_class": "notes-cell",
    },
]


MICE_PAGE_TABLE_COLUMN_SPECS = [
    {
        "key": "mouse_id",
        "label": "Mouse ID",
        "sort": "mouse_id",
        "url": lambda mouse: reverse("lab:mouse_record", args=[mouse.pk]),
        "link_class": "mouse-link",
        "cell_class": "mouse-id-cell",
    },
    {
        "key": "genetics_genotype_display",
        "label": "Genotype",
        "sort": "genotype",
        "cell_class": "compact-text-cell",
    },
    {
        "key": "get_sex_display",
        "label": "Sex",
        "sort": "sex",
        "cell_class": "tiny-cell",
    },
    {
        "key": "status",
        "label": "Status",
        "sort": "status",
        "cell_class": "status-cell",
    },
    {
        "key": "date_of_birth",
        "label": "DOB",
        "sort": "date_of_birth",
        "format": "date",
        "cell_class": "date-cell",
    },
    {
        "key": "cull_date",
        "label": "Cull date",
        "sort": "cull_date",
        "format": "date",
        "cell_class": "date-cell",
    },
    {
        "key": "protocol",
        "label": "Protocol",
        "sort": "protocol",
        "cell_class": "compact-text-cell",
    },
]


PROJECT_TABLE_COLUMN_SPECS = [
    {
        "key": "name",
        "label": "Name",
        "sort": "name",
        "url": lambda project: reverse("lab:experiments_page", args=[project.pk]),
        "link_class": "mouse-link",
        "cell_class": "compact-text-cell",
    },
    {
        "key": "lab_member",
        "label": "Lab Member",
        "sort": "lab_member",
        "cell_class": "compact-text-cell",
    },
    {
        "key": "description",
        "label": "Description",
        "sort": "description",
        "format": "linebreaks",
        "cell_class": "notes-cell",
    },
    {
        "key": "start_date",
        "label": "Start Date",
        "sort": "start_date",
        "format": "date",
        "cell_class": "date-cell",
    },
    {
        "key": "end_date",
        "label": "End Date",
        "sort": "end_date",
        "format": "date",
        "cell_class": "date-cell",
    },
]


PROJECT_FILTER_SPECS = [
    {
        "name": "name",
        "label": "Name",
        "type": "text",
        "lookup": "name__icontains",
        "placeholder": "Type project name",
    },
    {
        "name": "lab_member",
        "label": "Lab Member",
        "type": "text",
        "lookup": "lab_member__icontains",
        "placeholder": "Type lab member",
    },
]


EXPERIMENT_TABLE_COLUMN_SPECS = [
    {
        "key": "name",
        "label": "Name",
        "sort": "name",
        "url": lambda experiment: reverse(
            "lab:recordings_page",
            args=[experiment.project_id, experiment.pk],
        ),
        "link_class": "mouse-link",
        "cell_class": "compact-text-cell",
    },
    {
        "key": "recording_type",
        "label": "Type",
        "sort": "recording_type",
        "cell_class": "compact-text-cell",
    },
    {
        "key": "description",
        "label": "Description",
        "sort": "description",
        "format": "linebreaks",
        "cell_class": "notes-cell",
    },
    {
        "key": "start_date",
        "label": "Start Date",
        "sort": "start_date",
        "format": "date",
        "cell_class": "date-cell",
    },
    {
        "key": "end_date",
        "label": "End Date",
        "sort": "end_date",
        "format": "date",
        "cell_class": "date-cell",
    },
]


EXPERIMENT_FILTER_SPECS = [
    {
        "name": "name",
        "label": "Name",
        "type": "text",
        "lookup": "name__icontains",
        "placeholder": "Type experiment name",
    },
]


RECORDING_FILTER_SPECS = [
    {
        "name": "date_from",
        "label": "Start date",
        "type": "date",
    },
    {
        "name": "date_to",
        "label": "End date",
        "type": "date",
    },
    {
        "name": "mouse",
        "label": "Mouse",
        "type": "text",
        "lookup": "mice__mouse_id__icontains",
        "placeholder": "Type mouse ID",
    },
    {
        "name": "notes",
        "label": "Notes",
        "type": "text",
        "lookup": "notes__icontains",
        "placeholder": "Type notes",
    },
]


MOUSE_FILTER_SPECS = [

    {
        "name": "mouse_id",
        "label": "Mouse ID",
        "type": "text",
        "lookup": "mouse_id__icontains",
        "placeholder": "e.g. M123",
    },
    {
        "name": "dob",
        "label": "DOB",
        "type": "date",
        "lookup": "date_of_birth",
    },
    {
        "name": "protocol_start",
        "label": "Protocol start",
        "type": "date",
        "lookup": "protocol_start_date",
    },
    # {
    #     "name": "sex",
    #     "label": "Sex",
    #     "type": "select",
    #     "lookup": "sex__iexact",
    #     "choices": [("", "All"), *Mouse.SEX_CHOICES],
    # },
    {
        "name": "genotype",
        "label": "Genotype",
        "type": "text",
        "lookup": "genotype__icontains",
        "placeholder": "Type genotype",
    },
    {
        "name": "crossing",
        "label": "Crossing",
        "type": "text",
        "lookup": "crossing__icontains",
        "placeholder": "Type crossing",
        "autocomplete": CROSSING_FILTER_AUTOCOMPLETE,
    },
    {
        "name": "show_current_year",
        "label": "Show only current year",
        "type": "checkbox",
        "default": "1",
        "checkbox_value": "1",
        "unchecked_value": "0",
    },
    {
        "name": "severity_exceeded",
        "label": "Severity exceeded",
        "type": "select",
        "choices": [
            ("", "All"),
            ("1", "Exceeded only"),
        ],
    },
]


def build_mice_page_filter_specs():
    return [
        {
            "name": "mouse_id",
            "label": "Mouse ID",
            "type": "text",
            "lookup": "mouse_id__icontains",
            "placeholder": "e.g. M123",
        },
        {
            "name": "genotype",
            "label": "Genotype",
            "type": "text",
            "lookup": "genotype__icontains",
            "placeholder": "Type genotype",
        },
        {
            "name": "sex",
            "label": "Sex",
            "type": "select",
            "lookup": "sex__iexact",
            "choices": [("", "All"), *Mouse.SEX_CHOICES],
        },
        {
            "name": "status",
            "label": "Status",
            "type": "text",
            "lookup": "status__icontains",
            "placeholder": "Type status",
        },
        {
            "name": "dob",
            "label": "DOB",
            "type": "date",
            "lookup": "date_of_birth",
        },
        {
            "name": "cull_date",
            "label": "Cull date",
            "type": "date",
            "lookup": "cull_date",
        },
    ]


MOUSE_DETAIL_FIELD_SPECS = [
    {"key": "mouse_id", "label": "Mouse ID"},
    {"key": "date_of_birth", "label": "Date of birth", "format": "date"},
    {"key": "breeding_pair", "label": "Breeding pair"},
    {"key": "genetics_genotype_display", "label": "Genotype"},
    {"key": "genetics_crossing_display", "label": "Crossing"},
    {"key": "tattoo", "label": "Local identifier"},
    {"key": "protocol", "label": "Protocol"},
    {
        "key": "protocol_start_date",
        "label": "Protocol start date",
        "format": "date",
    },
    {"key": "get_severity_display", "label": "Max severity"},
    {"key": "get_sex_display", "label": "Sex"},
    {"key": "notes", "label": "Notes", "format": "linebreaks"},
    {"key": "cull_date", "label": "Cull date", "format": "date"},
]


PROCEDURE_RECORDING_COLUMN_SPECS = [
    {"key": "date", "label": "Date", "format": "date"},
    {"key": "procedure_type", "label": "Procedure type"},
    {"key": "lab_member", "label": "Lab member"},
    {"key": "lab_book_page", "label": "Lab-book page"},
]


BASE_RECORDING_COLUMN_SPECS = [
    {
        "key": "recording_date",
        "label": "Date",
        "format": "date",
        "url": lambda recording: selected_recording_url(recording),
        "link_class": "mouse-link",
    },
    {
        "key": "experiment__project",
        "label": "Project",
        "url": lambda recording: reverse(
            "lab:experiments_page",
            args=[recording.experiment.project_id],
        ),
        "link_class": "mouse-link",
    },
    {
        "key": "experiment__name",
        "label": "Experiment",
        "url": lambda recording: experiment_recordings_url(recording),
        "link_class": "mouse-link",
    },
    {"key": "sequence_number", "label": "Sequence"},
]


RECORDING_PATH_COLUMN_SPEC = [
    {
        "key": "data_paths",
        "label": "Paths",
        "format": "recording_paths",
        "cell_class": "path-cell",
    },
]


RECORDINGS_PAGE_BASE_COLUMN_SPECS = [
    {"key": "recording_date", "label": "Date", "format": "date"},
    {
        "key": "mice",
        "label": "Mice",
        "format": "recording_mice",
        "cell_class": "compact-text-cell",
    },
    {"key": "sequence_number", "label": "Sequence"},
]


RECORDINGS_PAGE_TRAILING_COLUMN_SPECS = [
    {
        "key": "data_paths",
        "label": "Recording Paths",
        "format": "recording_paths",
        "cell_class": "path-cell",
    },
    {"key": "notes", "label": "Notes", "format": "linebreaks", "cell_class": "notes-cell"},
]


def experiment_recordings_url(recording):
    return reverse(
        "lab:recordings_page",
        args=[recording.experiment.project_id, recording.experiment_id],
    )


def selected_recording_url(recording):
    query = urlencode({"selected_recording": recording.pk})
    return f"{experiment_recordings_url(recording)}?{query}#recording-{recording.pk}"


def read_filter_values(query_params, filter_specs):
    values = {}

    for spec in filter_specs:
        value = query_params.get(
            spec["name"],
            spec.get("default", ""),
        )
        values[spec["name"]] = str(value).strip()

    return values


def apply_filter_specs(queryset, filter_specs, values):
    for spec in filter_specs:
        lookup = spec.get("lookup")
        value = values.get(spec["name"])

        if lookup and value:
            queryset = queryset.filter(**{lookup: value})

    return queryset


def build_filter_form(filter_specs, values, hidden_fields=None, clear_url=None):
    fields = []
    has_autocomplete = False

    for spec in filter_specs:
        value = values.get(spec["name"], "")
        field = {
            "id": f"filter-{spec['name'].replace('_', '-')}",
            "name": spec["name"],
            "label": spec["label"],
            "type": spec["type"],
            "value": value,
            "placeholder": spec.get("placeholder", ""),
        }

        autocomplete = spec.get("autocomplete")

        if autocomplete:
            autocomplete_url = autocomplete.get("url")

            if not autocomplete_url:
                autocomplete_url = reverse(autocomplete["url_name"])

            field["autocomplete"] = {
                "url": autocomplete_url,
                "value_key": autocomplete.get("value_key", "value"),
                "label_key": autocomplete.get("label_key", "label"),
            }
            has_autocomplete = True

        if spec["type"] == "checkbox":
            field["checked"] = value not in {"", "0", "false", "False", "off"}
            field["checkbox_value"] = spec.get("checkbox_value", "1")
            field["unchecked_value"] = spec.get("unchecked_value", "0")
        elif spec["type"] == "select":
            field["choices"] = [
                {
                    "value": choice_value,
                    "label": choice_label,
                    "selected": str(choice_value) == str(value),
                }
                for choice_value, choice_label in spec["choices"]
            ]

        fields.append(field)

    return {
        "fields": fields,
        "hidden_fields": hidden_fields or [],
        "clear_url": clear_url,
        "has_autocomplete": has_autocomplete,
    }


def read_table_page_size(query_params):
    try:
        page_size = int(query_params.get("per_page", DEFAULT_TABLE_PAGE_SIZE))
    except (TypeError, ValueError):
        return DEFAULT_TABLE_PAGE_SIZE

    if page_size not in TABLE_PAGE_SIZE_CHOICES:
        return DEFAULT_TABLE_PAGE_SIZE

    return page_size


def build_table_page_size_control(query_params, current_page_size):
    preserved_params = query_params.copy()
    preserved_params.pop("page", None)
    preserved_params.pop("per_page", None)
    hidden_fields = [
        {
            "name": name,
            "value": value,
        }
        for name, values in preserved_params.lists()
        for value in values
    ]

    return {
        "name": "per_page",
        "label": "Rows",
        "current": current_page_size,
        "hidden_fields": hidden_fields,
        "options": [
            {
                "value": page_size,
                "label": str(page_size),
                "selected": page_size == current_page_size,
            }
            for page_size in TABLE_PAGE_SIZE_CHOICES
        ],
    }


def build_home_cards(user=None):
    cards = [
        {
            "title": "Experimental animals and procedures",
            "description": "View mice by licence and protocol and manage animal procedures.",
            "url": reverse("lab:procedure_page"),
        },
        {
            "title": "Projects",
            "description": (
                "Browse projects, experiments and associated recordings."
            ),
            "url": reverse("lab:projects_page"),
        },
        {
            "title": "Services",
            "description": "Experiments and data for the whole lab.",
            "url": reverse("lab:services_page"),
        },
        {
            "title": "Reports",
            "description": "Generate reports on mice, procedures and recordings.",
            "url": reverse("lab:reports_page"),
        },
    ]

    if user and user.is_authenticated and user.is_staff:
        cards.append(
            {
                "title": "Lines & Crossings",
                "description": (
                    "Manage species, lines and approved genetic crossings."
                ),
                "url": reverse("lab:lines_crossings_page"),
            }
        )
        cards.append(
            {
                "title": "Admin Console",
                "description": (
                    "Manage users and configure recording types and fields."
                ),
                "url": reverse("admin:index"),
            }
        )

    return cards


def build_detail_rows(obj, field_specs):
    return [
        {
            "label": spec["label"],
            "value": format_value(resolve_attr(obj, spec["key"]), spec.get("format")),
        }
        for spec in field_specs
    ]


def build_object_table(objects, column_specs, empty_message, table_class=""):
    columns = [
        {
            "label": spec["label"],
            "class": spec.get("header_class", spec.get("cell_class", "")),
        }
        for spec in column_specs
    ]
    rows = [
        {
            "cells": [
                build_cell(obj, spec)
                for spec in column_specs
            ],
        }
        for obj in objects
    ]

    return {
        "class": table_class,
        "columns": columns,
        "rows": rows,
        "empty_message": empty_message,
    }


def build_procedure_matrix_table(
    mice,
    procedure_types,
    current_sort,
    current_direction,
    query_params,
):
    procedure_types = list(procedure_types)
    columns = [
        {
            "class": "select-cell",
            "header_checkbox": {
                "aria_label": "Select all mice",
            },
        },
        *build_sortable_columns(
            MOUSE_TABLE_COLUMN_SPECS,
            current_sort,
            current_direction,
            query_params,
        ),
        *[
            {
                "label": procedure_type.name,
                "class": "procedure-cell",
            }
            for procedure_type in procedure_types
        ],
    ]

    rows = []

    for mouse in mice:
        procedures_by_type = {}

        for procedure in mouse.procedures.all():
            procedures_by_type.setdefault(
                procedure.procedure_type_id,
                [],
            ).append(procedure)
        cells = [
            {
                "class": "select-cell",
                "checkbox": {
                    "name": "selected_mouse",
                    "value": mouse.pk,
                    "aria_label": f"Select {mouse.mouse_id}",
                },
            },
            *[
                build_cell(mouse, spec)
                for spec in MOUSE_TABLE_COLUMN_SPECS
            ],
        ]

        for procedure_type in procedure_types:
            cells.append(
                {
                    "value": format_procedure_status(
                        procedures_by_type.get(procedure_type.pk),
                        severity_exceeded=mouse.severity_exceeded,
                    ),
                    "class": "procedure-cell",
                }
            )

        rows.append({"cells": cells})

    return {
        "class": "sticky-first procedure-matrix",
        "columns": columns,
        "rows": rows,
        "has_selectable_rows": bool(rows),
        "empty_message": "No mice are registered under this protocol.",
    }


def build_mice_table(
    mice,
    current_sort,
    current_direction,
    query_params,
):
    columns = [
        {
            "class": "select-cell",
            "header_checkbox": {
                "aria_label": "Select all mice",
            },
        },
        *build_sortable_columns(
            MICE_PAGE_TABLE_COLUMN_SPECS,
            current_sort,
            current_direction,
            query_params,
        ),
    ]
    rows = []

    for mouse in mice:
        rows.append(
            {
                "cells": [
                    {
                        "class": "select-cell",
                        "checkbox": {
                            "name": "selected_mouse",
                            "value": mouse.pk,
                            "aria_label": f"Select {mouse.mouse_id}",
                        },
                    },
                    *[
                        build_cell(mouse, spec)
                        for spec in MICE_PAGE_TABLE_COLUMN_SPECS
                    ],
                ],
            }
        )

    return {
        "class": "sticky-first mouse-table",
        "columns": columns,
        "rows": rows,
        "has_selectable_rows": bool(rows),
        "empty_message": "No mice match these filters.",
    }


def build_mouse_selector_table(
    mice,
    current_sort,
    current_direction,
    query_params,
    selected_mouse_ids=None,
):
    selected_mouse_ids = set(selected_mouse_ids or [])
    columns = [
        {
            "class": "select-cell",
            "header_checkbox": {
                "aria_label": "Select all mice",
            },
        },
        *build_sortable_columns(
            MICE_PAGE_TABLE_COLUMN_SPECS,
            current_sort,
            current_direction,
            query_params,
        ),
    ]
    rows = []

    for mouse in mice:
        rows.append(
            {
                "cells": [
                    {
                        "class": "select-cell",
                        "checkbox": {
                            "name": "selected_mouse",
                            "value": mouse.pk,
                            "aria_label": f"Select {mouse.mouse_id}",
                            "checked": mouse.pk in selected_mouse_ids,
                        },
                    },
                    *[
                        build_cell(mouse, spec)
                        for spec in MICE_PAGE_TABLE_COLUMN_SPECS
                    ],
                ],
            }
        )

    return {
        "class": "mouse-selector-table",
        "columns": columns,
        "rows": rows,
        "has_selectable_rows": bool(rows),
        "empty_message": "No mice match these filters.",
    }


def build_projects_table(
    projects,
    current_sort,
    current_direction,
    query_params,
    return_url=None,
):
    columns = [
        *build_sortable_columns(
            PROJECT_TABLE_COLUMN_SPECS,
            current_sort,
            current_direction,
            query_params,
        ),
        {
            "label": "Actions",
            "class": "action-cell",
        },
    ]
    rows = []

    for project in projects:
        edit_url = reverse("lab:edit_project", args=[project.pk])

        if return_url:
            edit_url = f"{edit_url}?{urlencode({'return_url': return_url})}"

        rows.append(
            {
                "cells": [
                    *[
                        build_cell(project, spec)
                        for spec in PROJECT_TABLE_COLUMN_SPECS
                    ],
                    {
                        "value": "Edit",
                        "class": "action-cell",
                        "url": edit_url,
                        "link_class": "button",
                    },
                ],
            }
        )

    return {
        "class": "projects-table",
        "columns": columns,
        "rows": rows,
        "empty_message": "No projects match these filters.",
    }


def build_experiments_table(
    experiments,
    current_sort,
    current_direction,
    query_params,
    return_url=None,
):
    columns = [
        *build_sortable_columns(
            EXPERIMENT_TABLE_COLUMN_SPECS,
            current_sort,
            current_direction,
            query_params,
        ),
        {
            "label": "Actions",
            "class": "action-cell",
        },
    ]
    rows = []

    for experiment in experiments:
        edit_url = reverse(
            "lab:edit_experiment",
            args=[experiment.project_id, experiment.pk],
        )

        if return_url:
            edit_url = f"{edit_url}?{urlencode({'return_url': return_url})}"

        rows.append(
            {
                "cells": [
                    *[
                        build_cell(experiment, spec)
                        for spec in EXPERIMENT_TABLE_COLUMN_SPECS
                    ],
                    {
                        "value": "Edit",
                        "class": "action-cell",
                        "url": edit_url,
                        "link_class": "button",
                    },
                ],
            }
        )

    return {
        "class": "experiments-table",
        "columns": columns,
        "rows": rows,
        "empty_message": "No experiments match these filters.",
    }


def build_service_experiment_cards(experiments):
    cards = []

    for experiment in experiments:
        description_parts = []

        if experiment.recording_type:
            description_parts.append(str(experiment.recording_type))

        if experiment.description:
            description_parts.append(experiment.description)

        cards.append(
            {
                "title": experiment.name,
                "description": (
                    " | ".join(description_parts)
                    or "Open service records."
                ),
                "url": reverse(
                    "lab:recordings_page",
                    args=[experiment.project_id, experiment.pk],
                ),
            }
        )

    return cards


def build_recording_type_table(recording_type, recordings):
    display_fields = [
        field
        for field in recording_type.fields.all()
        if field.active and field.display_in_table
    ]
    base_columns = [
        {
            "label": spec["label"],
            "class": spec.get("cell_class", ""),
        }
        for spec in BASE_RECORDING_COLUMN_SPECS
    ]
    field_columns = [
        {
            "label": recording_field_label(field),
            "class": "compact-text-cell",
        }
        for field in display_fields
    ]
    path_columns = [
        {
            "label": spec["label"],
            "class": spec.get("cell_class", ""),
        }
        for spec in RECORDING_PATH_COLUMN_SPEC
    ]
    rows = []

    for recording in recordings:
        rows.append(
            {
                "cells": [
                    *[
                        build_cell(recording, spec)
                        for spec in BASE_RECORDING_COLUMN_SPECS
                    ],
                    *[
                        {
                            "value": format_recording_field_value(
                                recording.values.get(field.key),
                                field.data_type,
                            ),
                            "class": "compact-text-cell",
                        }
                        for field in display_fields
                    ],
                    *[
                        build_cell(recording, spec)
                        for spec in RECORDING_PATH_COLUMN_SPEC
                    ],
                ],
            }
        )

    return {
        "class": "recordings-table",
        "columns": [
            *base_columns,
            *field_columns,
            *path_columns,
        ],
        "rows": rows,
        "empty_message": (
            f"No {recording_type.display_plural_name.lower()} recorded."
        ),
    }


def build_recordings_table(
    recordings,
    recording_type,
    user=None,
    selected_recording_id=None,
):
    display_fields = []

    if recording_type is not None:
        display_fields = [
            field
            for field in recording_type.fields.all()
            if field.active and field.display_in_table
        ]

    columns = [
        {
            "class": "select-cell",
            "header_checkbox": {
                "aria_label": "Select all recordings",
            },
        },
        *[
            {
                "label": spec["label"],
                "class": spec.get("cell_class", ""),
            }
            for spec in RECORDINGS_PAGE_BASE_COLUMN_SPECS
        ],
        *[
            {
                "label": recording_field_label(field),
                "class": "compact-text-cell",
            }
            for field in display_fields
        ],
        *[
            {
                "label": spec["label"],
                "class": spec.get("cell_class", ""),
            }
            for spec in RECORDINGS_PAGE_TRAILING_COLUMN_SPECS
        ],
        {"label": "Edit", "class": "action-cell"},
        {"label": "Delete", "class": "action-cell"},
    ]
    rows = []

    for recording in recordings:
        can_modify = recording.can_be_modified_by(user)
        is_selected = recording.pk == selected_recording_id
        edit_url = reverse(
            "lab:edit_recording",
            args=[
                recording.experiment.project_id,
                recording.experiment_id,
                recording.pk,
            ],
        )
        delete_url = reverse(
            "lab:delete_recording",
            args=[
                recording.experiment.project_id,
                recording.experiment_id,
                recording.pk,
            ],
        )
        rows.append(
            {
                "id": f"recording-{recording.pk}",
                "cells": [
                    {
                        "class": "select-cell",
                        "checkbox": (
                            {
                                "name": "selected_recording",
                                "value": recording.pk,
                                "aria_label": f"Select recording {recording.pk}",
                                "checked": is_selected,
                            }
                            if can_modify
                            else None
                        ),
                        "value": EMPTY_VALUE if not can_modify else "",
                    },
                    *[
                        build_cell(recording, spec)
                        for spec in RECORDINGS_PAGE_BASE_COLUMN_SPECS
                    ],
                    *[
                        {
                            "value": format_recording_field_value(
                                recording.values.get(field.key),
                                field.data_type,
                            ),
                            "class": "compact-text-cell",
                        }
                        for field in display_fields
                    ],
                    *[
                        build_cell(recording, spec)
                        for spec in RECORDINGS_PAGE_TRAILING_COLUMN_SPECS
                    ],
                    {
                        "value": "Edit" if can_modify else EMPTY_VALUE,
                        "class": "action-cell",
                        "url": edit_url if can_modify else None,
                        "link_class": "button",
                    },
                    {
                        "value": "Delete" if can_modify else EMPTY_VALUE,
                        "class": "action-cell",
                        "url": delete_url if can_modify else None,
                        "link_class": "button button-danger",
                    },
                ],
            }
        )

    return {
        "class": "recordings-table",
        "columns": columns,
        "rows": rows,
        "has_selectable_rows": any(
            row["cells"][0].get("checkbox")
            for row in rows
        ),
        "empty_message": "No recordings match these filters.",
    }


def recording_field_label(field):
    if field.units:
        return f"{field.label} ({field.units})"

    return field.label


def format_recording_field_value(value, data_type):
    if value is None or value == "":
        return EMPTY_VALUE

    if data_type == "boolean":
        return "Yes" if value else "No"

    if data_type == "date":
        if isinstance(value, date):
            return value.strftime("%d/%m/%Y")

        try:
            return date.fromisoformat(value).strftime("%d/%m/%Y")
        except (TypeError, ValueError):
            return value

    return value


def build_mouse_record_sections(mouse, procedures, recording_groups):
    sections = [
        {
            "title": "General information",
            "details": {
                "rows": build_detail_rows(mouse, MOUSE_DETAIL_FIELD_SPECS),
            },
        },
        {
            "title": "Procedures",
            "table": build_object_table(
                procedures,
                PROCEDURE_RECORDING_COLUMN_SPECS,
                "No procedures recorded.",
            ),
        },
    ]

    for group in recording_groups:
        recording_type = group["recording_type"]
        sections.append(
            {
                "title": recording_type.display_plural_name,
                "table": build_recording_type_table(
                    recording_type,
                    group["recordings"],
                ),
            },
        )

    return sections


def build_record_count(page_obj, noun):
    return {
        "start": page_obj.start_index(),
        "end": page_obj.end_index(),
        "count": page_obj.paginator.count,
        "noun": noun,
    }


def build_pagination(page_obj, query_params):
    if page_obj.paginator.num_pages <= 1:
        return None

    return {
        "number": page_obj.number,
        "num_pages": page_obj.paginator.num_pages,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
        "first_url": page_url(query_params, 1),
        "previous_url": (
            page_url(query_params, page_obj.previous_page_number())
            if page_obj.has_previous()
            else ""
        ),
        "next_url": (
            page_url(query_params, page_obj.next_page_number())
            if page_obj.has_next()
            else ""
        ),
        "last_url": page_url(query_params, page_obj.paginator.num_pages),
    }


def sortable_fields():
    return {
        spec["sort"]
        for spec in MOUSE_TABLE_COLUMN_SPECS
        if spec.get("sort")
    }


def mice_page_sortable_fields():
    return {
        spec["sort"]
        for spec in MICE_PAGE_TABLE_COLUMN_SPECS
        if spec.get("sort")
    }


def project_page_sortable_fields():
    return {
        spec["sort"]
        for spec in PROJECT_TABLE_COLUMN_SPECS
        if spec.get("sort")
    }


def experiment_page_sortable_fields():
    return {
        spec["sort"]
        for spec in EXPERIMENT_TABLE_COLUMN_SPECS
        if spec.get("sort")
    }


def build_sortable_columns(
    column_specs,
    current_sort,
    current_direction,
    query_params,
):
    columns = []

    for spec in column_specs:
        sort_key = spec.get("sort")
        column = {
            "label": spec["label"],
            "class": spec.get("header_class", spec.get("cell_class", "")),
        }

        if sort_key:
            next_direction = (
                "desc"
                if current_sort == sort_key and current_direction == "asc"
                else "asc"
            )
            column["sort_url"] = query_url(
                query_params,
                page=None,
                sort=sort_key,
                direction=next_direction,
            )

            if current_sort == sort_key:
                column["sort_state"] = current_direction

        columns.append(column)

    return columns


def build_cell(obj, spec):
    if spec.get("format") == "crossing_type_summary":
        value = format_crossing_type_summary(obj)
    elif spec.get("format") == "recording_mice":
        value = format_recording_mice_links(obj)
    else:
        value = format_value(resolve_attr(obj, spec["key"]), spec.get("format"))
    cell = {
        "value": value,
        "class": spec.get("cell_class", ""),
    }

    url_factory = spec.get("url")
    if url_factory:
        cell["url"] = url_factory(obj)
        cell["link_class"] = spec.get("link_class", "")

    return cell


def format_recording_mice_links(recording):
    mice = sorted(
        recording.mice.all(),
        key=lambda mouse: mouse.mouse_id.lower(),
    )

    if not mice:
        return EMPTY_VALUE

    return format_html_join(
        "",
        '<a class="mouse-link" href="{}">{}</a><br>',
        (
            (
                reverse("lab:mouse_record", args=[mouse.pk]),
                mouse.mouse_id,
            )
            for mouse in mice
        ),
    )


def format_crossing_type_summary(mouse):
    crossing = mouse.genetics_crossing_display or "n/a"
    crossing_type = mouse.genetics_crossing_type_display or "n/a"

    return format_html("{}<br>{}", crossing, crossing_type)


def resolve_attr(obj, path):
    value = obj

    for part in path.split("__"):
        if value is None:
            return None

        value = getattr(value, part)

        if callable(value):
            value = value()

    return value


def format_value(value, value_format=None):
    if value is None or value == "" or value == []:
        return EMPTY_VALUE

    if value_format == "date":
        return value.strftime("%d/%m/%Y")

    if value_format == "linebreaks":
        return linebreaksbr(value)

    if value_format == "recording_paths":
        return format_html_join(
            format_html("<br>"),
            "{}",
            ((path,) for path in value),
        )

    if value_format == "yesno":
        return "Yes" if value else "No"

    return value


def format_procedure_status(procedures, severity_exceeded=False):
    procedures = list(procedures or [])

    if not procedures:
        return format_html('<span class="empty-mark">&mdash;</span>')

    procedure = latest_procedure(procedures)
    procedure_count = len(procedures)
    checkmark_class = (
        "checkmark checkmark-danger"
        if severity_exceeded
        else "checkmark"
    )
    parts = [
        format_html(
            '<div class="{}">&#10003;</div>',
            checkmark_class,
        )
    ]

    if procedure_count > 1:
        parts.append(
            format_html(
                '<div class="procedure-detail">{} procedures total</div>',
                procedure_count,
            )
        )

    if procedure.lab_member:
        parts.append(
            format_html(
                '<div class="procedure-detail">{}</div>',
                procedure.lab_member,
            )
        )

    if procedure.lab_book_page:
        parts.append(
            format_html(
                '<div class="procedure-detail">Page {}</div>',
                procedure.lab_book_page,
            )
        )

    return format_html_join("", "{}", ((part,) for part in parts))


def latest_procedure(procedures):
    return max(
        procedures,
        key=lambda procedure: (
            procedure.date or date.min,
            procedure.pk or 0,
        ),
    )


def page_url(query_params, page_number):
    return query_url(query_params, page=page_number)


def query_url(query_params, **updates):
    params = query_params.copy()

    for key, value in updates.items():
        params.pop(key, None)
        if value is not None and value != "":
            params[key] = value

    encoded = params.urlencode()
    if not encoded:
        return "?"

    return f"?{encoded}"
