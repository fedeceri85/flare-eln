"""Read-only recording exports scoped to an experiment."""
from urllib.parse import parse_qs, urlsplit

from django import forms
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from ..models import UsageEvent
from ..usage import record_usage
from ..recording_reports import recording_report_data, render_recording_excel, render_recording_pdf
from .common import experiment_for_project_or_404, experiment_recordings_url, safe_return_url
from .recordings import order_recordings


class RecordingReportForm(forms.Form):
    scope = forms.ChoiceField(label="Recordings to export", choices=[
        ("all", "Entire experiment"), ("selected", "Selected recordings only"),
    ])
    format = forms.ChoiceField(label="Report format", choices=[("pdf", "PDF"), ("xlsx", "Excel (.xlsx)")], initial="pdf")


@require_http_methods(["GET", "POST"])
def recording_report(request, project_pk, experiment_pk):
    project, experiment = experiment_for_project_or_404(project_pk, experiment_pk)
    source = request.POST if request.method == "POST" else request.GET
    raw_ids = list(dict.fromkeys(source.getlist("selected_recording")))
    try:
        ids = {int(value) for value in raw_ids}
    except (ValueError, TypeError) as exc:
        raise Http404("Invalid recording selection.") from exc
    if experiment.recordings.filter(pk__in=ids).count() != len(ids):
        raise Http404("One or more selected recordings no longer belong to this experiment.")
    return_url = safe_return_url(request, experiment_recordings_url(project, experiment))
    downloading = request.method == "POST" and "download" in source
    form = RecordingReportForm(source if downloading else None, initial={"scope": "selected" if ids else "all", "format": "pdf"})
    if downloading and form.is_valid():
        recordings = experiment.recordings.select_related("experiment", "recording_type").prefetch_related("mice")
        if form.cleaned_data["scope"] == "selected":
            if not ids:
                form.add_error("scope", "Select at least one recording in the table before exporting selected recordings.")
            recordings = recordings.filter(pk__in=ids)
        ordering = parse_qs(urlsplit(return_url).query)
        recordings, _, _ = order_recordings(recordings, experiment.recording_type,
                                           ordering.get("sort", ["recording_date"])[0], ordering.get("direction", ["desc"])[0])
        if not form.errors:
            data = recording_report_data(experiment, recordings)
            if not data["rows"]:
                form.add_error("scope", "There are no recordings to export.")
            else:
                output_format = form.cleaned_data["format"]
                content = render_recording_pdf(data, project.name, experiment.name) if output_format == "pdf" else render_recording_excel(data, project.name, experiment.name)
                content_type = "application/pdf" if output_format == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                response = HttpResponse(content, content_type=content_type)
                response["Content-Disposition"] = f'attachment; filename="experiment-{experiment.pk}-recordings-{timezone.localdate():%Y-%m-%d}.{output_format}"'
                response["Cache-Control"] = "private, no-store"
                record_usage(
                    UsageEvent.REPORT_EXPORTED, request.user, source="report",
                    target_type="experiment", reference=str(experiment.pk),
                    object_count=len(data["rows"]), format=output_format,
                )
                return response
    response = render(request, "lab/recording_report.html", {
        "project": project, "experiment": experiment, "form": form,
        "selected_ids": sorted(ids), "selected_count": len(ids),
        "total_count": experiment.recordings.count(), "return_url": return_url,
    }, status=400 if form.errors else 200)
    response["Cache-Control"] = "private, no-store"
    return response
