'Reports views and helpers.'

from django.shortcuts import render
from django.urls import reverse

from ..forms import HomeOfficeReturnForm
from ..models import MAX_SEVERITY_CHOICES, Mouse, Protocol
from ..ui import build_home_cards


HOME_OFFICE_RETURN_REPORT = "home_office_return"


HOME_OFFICE_WARNING = (
    "Mice are assigned to this return based on protocol start date, "
    "not protocol end date."
)


HOME_OFFICE_SEVERITY_KEYS = {
    severity_value: severity_value.lower()
    for severity_value, _ in MAX_SEVERITY_CHOICES
}


HOME_OFFICE_CROSSING_TYPE_LABELS = [
    label
    for _, label in Mouse.CROSSING_CHOICES
]


HOME_OFFICE_UNKNOWN_CROSSING_TYPE = "Unknown"


LEGACY_HOME_OFFICE_CROSSING_TYPE_LABELS = {
    "Double_triple": "Multiple",
}


def empty_home_office_counts():
    return {
        "total": 0,
        **dict.fromkeys(HOME_OFFICE_SEVERITY_KEYS.values(), 0),
        "not_set": 0,
        "exceeded": 0,
    }


def empty_crossing_type_counts():
    return {
        label: 0
        for label in [
            *HOME_OFFICE_CROSSING_TYPE_LABELS,
            HOME_OFFICE_UNKNOWN_CROSSING_TYPE,
        ]
    }


def crossing_type_label(mouse):
    if mouse.crossing_type in LEGACY_HOME_OFFICE_CROSSING_TYPE_LABELS:
        return LEGACY_HOME_OFFICE_CROSSING_TYPE_LABELS[mouse.crossing_type]

    return mouse.get_crossing_type_display() or HOME_OFFICE_UNKNOWN_CROSSING_TYPE


def increment_crossing_type_counts(counts, mouse):
    label = crossing_type_label(mouse)
    counts[label] = counts.get(label, 0) + 1


def crossing_type_rows(counts):
    return [
        {
            "label": label,
            "count": counts.get(label, 0),
        }
        for label in [
            *HOME_OFFICE_CROSSING_TYPE_LABELS,
            HOME_OFFICE_UNKNOWN_CROSSING_TYPE,
        ]
    ]


def add_mouse_to_home_office_counts(counts, mouse):
    counts["total"] += 1
    severity_key = HOME_OFFICE_SEVERITY_KEYS.get(mouse.severity)

    if severity_key:
        counts[severity_key] += 1
    else:
        counts["not_set"] += 1

    if mouse.severity_exceeded:
        counts["exceeded"] += 1


def build_report_cards():
    return [
        {
            "title": "Mice",
            "description": "Browse, filter and manage all mouse records.",
            "url": reverse("lab:mice_page"),
        },
        {
            "title": "Home Office Return",
            "description": (
                "Generate project licence return counts by protocol "
                "procedure, and crossing type."
            ),
            "url": (
                f"{reverse('lab:reports_page')}"
                f"?report={HOME_OFFICE_RETURN_REPORT}"
            ),
        },
    ]


def build_home_office_return_text(report):
    lines = [
        "Home Office Return",
        f"Project licence: {report['licence_reference']}",
        (
            "Date range: "
            f"{report['start_date']:%d/%m/%Y} to "
            f"{report['end_date']:%d/%m/%Y}"
        ),
        "",
        f"Warning: {report['warning']}",
        "",
        "Total numbers",
        f"Mice under procedure: {report['total']['total']}",
        *(f"{row['label']}: {row['count']}" for row in report["total"]["severity_rows"]),
        f"Severity not set: {report['total']['not_set']}",
        f"Surpassed severity threshold: {report['total']['exceeded']}",
        "",
        "Total by crossing type",
    ]

    for row in report["total_crossing_type_rows"]:
        lines.append(f"{row['label']}: {row['count']}")

    lines.extend([
        "",
        "Breakdown by protocol",
    ])

    for row in report["protocol_rows"]:
        max_severity = row["max_severity"] or "Not set"
        lines.extend(
            [
                "",
                row["protocol_label"],
                f"Max severity: {max_severity}",
                f"Mice under procedure: {row['total']}",
                *(f"{severity['label']}: {severity['count']}" for severity in row["severity_rows"]),
                f"Severity not set: {row['not_set']}",
                f"Surpassed severity threshold: {row['exceeded']}",
                "By crossing type:",
            ]
        )
        lines.extend(
            f"{crossing_type_row['label']}: {crossing_type_row['count']}"
            for crossing_type_row in row["crossing_type_rows"]
        )

    lines.extend([
        "",
        "Breakdown by procedure",
    ])

    for row in report["procedure_rows"]:
        lines.extend(
            [
                "",
                row["procedure_label"],
                f"Mice under procedure: {row['total']}",
                *(f"{severity['label']}: {severity['count']}" for severity in row["severity_rows"]),
                f"Severity not set: {row['not_set']}",
                f"Surpassed severity threshold: {row['exceeded']}",
            ]
        )

    return "\n".join(lines)


def build_home_office_return_report(
    licence_reference,
    start_date,
    end_date,
    output_mode,
):
    protocols = list(
        Protocol.objects.filter(
            licence_reference=licence_reference,
            allows_regulated_procedures=True,
        ).order_by(
            "protocol_number",
            "protocol_id",
        )
    )
    total_counts = empty_home_office_counts()
    total_crossing_type_counts = empty_crossing_type_counts()
    counts_by_protocol = {
        protocol.pk: empty_home_office_counts()
        for protocol in protocols
    }
    crossing_type_counts_by_protocol = {
        protocol.pk: empty_crossing_type_counts()
        for protocol in protocols
    }
    mice = list(
        Mouse.objects.select_related("protocol")
        .prefetch_related("procedures__procedure_type")
        .filter(
            protocol__licence_reference=licence_reference,
            protocol_start_date__gte=start_date,
            protocol_start_date__lte=end_date,
            protocol__allows_regulated_procedures=True,
        )
        .order_by("protocol__protocol_number", "mouse_id")
    )
    counts_by_procedure_type = {}
    procedure_types_by_id = {}

    for mouse in mice:
        add_mouse_to_home_office_counts(total_counts, mouse)
        increment_crossing_type_counts(total_crossing_type_counts, mouse)
        protocol_counts = counts_by_protocol.get(mouse.protocol_id)

        if protocol_counts is not None:
            add_mouse_to_home_office_counts(protocol_counts, mouse)
            increment_crossing_type_counts(
                crossing_type_counts_by_protocol[mouse.protocol_id],
                mouse,
            )

        seen_procedure_type_ids = set()
        for procedure in mouse.procedures.all():
            procedure_type_id = procedure.procedure_type_id

            if procedure_type_id in seen_procedure_type_ids:
                continue

            seen_procedure_type_ids.add(procedure_type_id)
            procedure_types_by_id[procedure_type_id] = procedure.procedure_type
            procedure_counts = counts_by_procedure_type.setdefault(
                procedure_type_id,
                empty_home_office_counts(),
            )
            add_mouse_to_home_office_counts(procedure_counts, mouse)

    protocol_rows = []
    for protocol in protocols:
        counts = counts_by_protocol[protocol.pk]
        protocol_rows.append(
            {
                **counts,
                "protocol": protocol,
                "protocol_label": str(protocol),
                "max_severity": protocol.max_severity,
                "crossing_type_rows": crossing_type_rows(
                    crossing_type_counts_by_protocol[protocol.pk],
                ),
            }
        )

    procedure_rows = []
    for procedure_type_id, procedure_type in sorted(
        procedure_types_by_id.items(),
        key=lambda item: (item[1].name.lower(), item[0]),
    ):
        counts = counts_by_procedure_type[procedure_type_id]
        procedure_rows.append(
            {
                **counts,
                "procedure_type": procedure_type,
                "procedure_label": str(procedure_type),
            }
        )

    for counts in [total_counts, *protocol_rows, *procedure_rows]:
        counts["severity_rows"] = [
            {"label": label, "count": counts[HOME_OFFICE_SEVERITY_KEYS[value]]}
            for value, label in MAX_SEVERITY_CHOICES
        ]

    report = {
        "severity_labels": [label for _, label in MAX_SEVERITY_CHOICES],
        "protocol_column_count": 5 + len(MAX_SEVERITY_CHOICES) + len(total_crossing_type_counts),
        "procedure_column_count": 4 + len(MAX_SEVERITY_CHOICES),
        "licence_reference": licence_reference,
        "start_date": start_date,
        "end_date": end_date,
        "output_mode": output_mode,
        "warning": HOME_OFFICE_WARNING,
        "total": total_counts,
        "crossing_type_labels": [
            *HOME_OFFICE_CROSSING_TYPE_LABELS,
            HOME_OFFICE_UNKNOWN_CROSSING_TYPE,
        ],
        "total_crossing_type_rows": crossing_type_rows(
            total_crossing_type_counts,
        ),
        "protocol_rows": protocol_rows,
        "procedure_rows": procedure_rows,
    }
    report["text"] = build_home_office_return_text(report)

    return report


def reports_page(request):
    selected_report = request.GET.get("report", "")
    home_office_form = None
    home_office_report = None
    report_query_keys = {
        "licence_reference",
        "start_date",
        "end_date",
        "output_mode",
    }

    if selected_report == HOME_OFFICE_RETURN_REPORT:
        has_report_params = any(
            key in request.GET
            for key in report_query_keys
        )
        home_office_form = HomeOfficeReturnForm(
            request.GET if has_report_params else None
        )

        if has_report_params and home_office_form.is_valid():
            home_office_report = build_home_office_return_report(
                licence_reference=home_office_form.cleaned_data[
                    "licence_reference"
                ],
                start_date=home_office_form.cleaned_data["start_date"],
                end_date=home_office_form.cleaned_data["end_date"],
                output_mode=home_office_form.cleaned_data["output_mode"],
            )

    return render(
        request,
        "lab/reports_page.html",
        {
            "selected_report": selected_report,
            "report_cards": build_report_cards(),
            "home_office_report_key": HOME_OFFICE_RETURN_REPORT,
            "home_office_form": home_office_form,
            "home_office_report": home_office_report,
        },
    )


def home(request):
    return render(
        request,
        "lab/home.html",
        {
            "cards": build_home_cards(request.user),
        },
    )
