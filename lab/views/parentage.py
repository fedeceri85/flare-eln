"""Authenticated family summaries and redirects from retired matching screens."""
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db.models import F, Q
from django.views.decorators.http import require_safe

from ..family import build_family_sections, build_parent_groups, offspring_groups
from ..models import Mouse
from .common import animal_summary_context, edit_selected_mice_all_url, safe_return_url, selected_mouse_ids, selected_mice_for_ids


@require_safe
def retired_parentage_review(request, mouse_pk=None):
    """Old bookmarks lead to maintenance; retired POST actions cannot save links."""
    return redirect('lab:maintenance')


def render_combined(request, mice, return_url):
    return render(request, "lab/animal_summary.html", {
        **animal_summary_context(request, mice, summary_url=request.get_full_path(), edit_url=edit_selected_mice_all_url()),
        "return_url": return_url,
        "empty_message": "No linked animals are recorded for this selection.",
    })


@require_safe
def view_selected_mice_all(request):
    try:
        mice = selected_mice_for_ids(selected_mouse_ids(request))
    except Mouse.DoesNotExist:
        raise Http404("One or more selected animals no longer exist.")
    return render_combined(request, mice, safe_return_url(request, reverse("lab:mice_page")))


@require_safe
def animal_relatives(request, mouse_pk):
    mouse = get_object_or_404(Mouse, pk=mouse_pk)
    return_url = safe_return_url(request, reverse("lab:animal_summary", args=[mouse.pk]))
    relation = request.GET.get("relation", "parents")
    if relation == "parents":
        ids = list(dict.fromkeys(pk for pk in (mouse.mother_id, mouse.father_id) if pk))
        by_id = {m.pk: m for m in Mouse.objects.filter(pk__in=ids).select_related("protocol")}
        mice = [by_id[pk] for pk in ids if pk in by_id]
    elif relation == "offspring":
        query = Q(mother_id=mouse.pk) | Q(father_id=mouse.pk)
        group_key = request.GET.get("group")
        if group_key:
            group = next((group for group in offspring_groups([mouse.pk], previews=False)[mouse.pk] if group["key"] == group_key), None)
            if group is None:
                raise Http404("This offspring group no longer exists.")
            role, identity = group["role"], group["identity"]
            other = "father" if role == "mother" else "mother"
            query = Q(**{f"{role}_id": mouse.pk})
            if identity[0] == "linked":
                query &= Q(**{f"{other}_id": identity[1]})
            else:
                query &= Q(**{f"{other}_id__isnull": True, f"{other}_external_id": identity[1] if identity[0] == "external" else ""})
        mice = list(Mouse.objects.filter(query).select_related("protocol").order_by(F("date_of_birth").asc(nulls_last=True), "mouse_id"))
    else:
        raise Http404("Unknown family relationship.")
    return render_combined(request, mice, return_url)


def render_family_fragment(request, template, context):
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        context = {**context, "fragment_template": template}
        template = "lab/family_details_page.html"
    response = render(request, template, context)
    response["Cache-Control"] = "private, no-store"
    return response


@require_safe
def family_fragment(request):
    try:
        mice = selected_mice_for_ids(selected_mouse_ids(request))
    except Mouse.DoesNotExist:
        raise Http404("One or more selected animals no longer exist.")
    return_url = safe_return_url(request, reverse("lab:mice_page"))
    families = build_family_sections(mice, return_url)
    response = render_family_fragment(request, "lab/components/animal_family.html", {
        "family_sections": families, "parent_groups": build_parent_groups(mice, return_url, families=families),
        "family_return_url": return_url,
    })
    return response
