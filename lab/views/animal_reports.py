"""Authenticated report downloads for the animal summary selection."""
from django import forms
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from ..animal_reports import report_groups, render_excel, render_pdf
from ..models import Mouse, UsageEvent
from ..usage import record_usage
from ..sharing import share_animals
from .common import safe_return_url, selected_mice_for_ids, selected_mouse_ids, url_with_query_params


class AnimalReportForm(forms.Form):
    format = forms.ChoiceField(label="Report format", choices=[("pdf", "PDF"), ("xlsx", "Excel (.xlsx)")], initial="pdf")


@require_http_methods(["GET", "POST"])
def animal_report(request):
    mouse_ids = selected_mouse_ids(request)
    if not 1 <= len(mouse_ids) <= 500:
        return HttpResponse("Select between 1 and 500 animals to report.", status=400)
    try:
        mice = selected_mice_for_ids(mouse_ids)
    except Mouse.DoesNotExist as exc:
        raise Http404("One or more selected animals are no longer available.") from exc
    return_url = safe_return_url(request, url_with_query_params(reverse("lab:view_selected_mice_all"), selected_mouse=mouse_ids))
    form = AnimalReportForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        groups = report_groups(mice)
        try:
            link = share_animals(mouse_ids, "", request.user, source="report")
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            output_format = form.cleaned_data["format"]
            content = render_pdf(groups, link.code) if output_format == "pdf" else render_excel(groups, link.code)
            content_type = "application/pdf" if output_format == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            response = HttpResponse(content, content_type=content_type)
            response["Content-Disposition"] = f'attachment; filename="animal-summary-{timezone.localdate():%Y-%m-%d}.{output_format}"'
            response["Cache-Control"] = "private, no-store"
            record_usage(
                UsageEvent.REPORT_EXPORTED, request.user, source="report",
                target_type="animals", reference=link.code,
                object_count=len(mice), format=output_format,
            )
            return response
    response = render(request, "lab/animal_report.html", {
        "form": form, "selected_mice": mice, "return_url": return_url,
    }, status=400 if form.errors else 200)
    response["Cache-Control"] = "private, no-store"
    return response
