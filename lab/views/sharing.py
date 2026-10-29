"""Share codes never grant permissions: every endpoint requires login."""
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_safe

from ..models import SharedLink
from ..sharing import normalize_share_code, share_animals, share_recording
from ..ui import selected_recording_url
from .common import url_with_query_params


class OpenShareCodeForm(forms.Form):
    code = forms.CharField(max_length=80, label="Shared code", widget=forms.TextInput(attrs={
        "placeholder": "m-maple-otter", "autocomplete": "off", "spellcheck": "false",
        "autocapitalize": "none",
    }))


def share_payload(request, link):
    return {"code": link.code, "url": request.build_absolute_uri(reverse("lab:shared_view", args=[link.code]))}


@require_POST
def create_animal_share(request):
    try:
        link = share_animals(request.POST.getlist("selected_mouse"), request.POST.get("mouse_editor_tab", ""), request.user)
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)
    return JsonResponse(share_payload(request, link))


@require_POST
def create_recording_share(request, recording_pk):
    try:
        link = share_recording(recording_pk, request.user)
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)
    return JsonResponse(share_payload(request, link))


def find_share(value):
    try:
        key = normalize_share_code(value)
        return SharedLink.objects.select_related("recording__experiment").get(lookup_key=key), None, 200
    except (ValidationError, SharedLink.DoesNotExist):
        return None, "Code not found. Check the code and try again.", 404


def destination(link):
    if link.kind == SharedLink.RECORDING:
        if link.recording_id is None:
            return None, "This recording is no longer available.", 0
        return selected_recording_url(link.recording), None, 0
    animal_ids = list(link.animals.order_by("mouse_id").values_list("mouse_id", flat=True))
    live_ids = [pk for pk in animal_ids if pk is not None]
    if not live_ids:
        return None, "These animals are no longer available.", 0
    tab = "shared" if len(live_ids) == 1 and link.animal_tab == "individual" else link.animal_tab
    return url_with_query_params(
        reverse("lab:view_selected_mice_all"), selected_mouse=live_ids, mouse_editor_tab=tab,
    ), None, len(animal_ids) - len(live_ids)


@require_safe
def shared_view(request, code):
    link, error, status = find_share(code)
    if link is not None:
        url, error, missing = destination(link)
        if url:
            if missing:
                messages.warning(request, f"{missing} animal{'s' if missing != 1 else ''} in this selection {'are' if missing != 1 else 'is'} no longer available.")
            response = redirect(url)
            response["Cache-Control"] = "private, no-store"
            return response
        status = 410
    return render(request, "lab/share_unavailable.html", {"share_error": error}, status=status)


@require_safe
def open_share_code(request):
    form = OpenShareCodeForm(request.GET if "code" in request.GET else None)
    error, status = None, 200
    if form.is_bound:
        if form.is_valid():
            link, error, status = find_share(form.cleaned_data["code"])
            if link is not None:
                url, error, _ = destination(link)
                if url:
                    short_url = reverse("lab:shared_view", args=[link.code])
                    if request.headers.get("Accept") == "application/json":
                        return JsonResponse({"url": short_url})
                    return redirect(short_url)
                status = 410
        else:
            error, status = "Enter a valid share code.", 400
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"error": error or "Enter a share code."}, status=status if error else 400)
    return render(request, "lab/open_share_code.html", {"form": form, "code_error": error}, status=status)
