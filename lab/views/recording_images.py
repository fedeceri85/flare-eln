"""Authenticated image management and a JSON-backed gallery with an HTML fallback."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from ..forms.recording_images import ImageStorageForm, RecordingImageUploadForm
from ..models import RecordingImage
from ..recording_images import activate_directory, available_images, read_image, save_images
from .common import experiment_for_project_or_404, experiment_recordings_url, safe_return_url
from .recordings import recording_for_experiment_or_404, require_recording_modify_permission
from .settings import administrator_required


@administrator_required
@require_http_methods(["GET", "HEAD", "POST"])
def image_storage_settings(request):
    form = ImageStorageForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            activate_directory(form.cleaned_data["directory"])
        except ValidationError as exc:
            form.add_error("directory", exc)
        else:
            messages.success(request, "Image storage updated. New uploads will use this folder.")
            return redirect("lab:image_storage_settings")
    return render(request, "lab/settings_form.html", {
        "form": form, "title": "Image storage", "parent_url": reverse("lab:settings_page"),
        "help_note": "Changes apply to new uploads only. In Docker, choose a folder within the mounted media folder. Images are not included in application backups.",
    })


def gallery_payload(recording, user):
    return {
        "images": [{
            "id": str(image.pk), "name": image.original_name,
            "width": image.width, "height": image.height,
            "url": reverse("lab:recording_image_file", args=[image.pk]),
            "download_url": reverse("lab:recording_image_file", args=[image.pk]) + "?download=1",
        } for image in available_images(recording)],
        "can_modify": recording.can_be_modified_by(user),
    }


@require_http_methods(["GET", "HEAD", "POST"])
def recording_images(request, project_pk, experiment_pk, recording_pk):
    project, experiment = experiment_for_project_or_404(project_pk, experiment_pk)
    recording = recording_for_experiment_or_404(experiment, recording_pk)
    ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    upload_form = RecordingImageUploadForm()
    errors = []
    if request.method == "POST":
        require_recording_modify_permission(request.user, recording)
        action = request.POST.get("action")
        if action == "upload":
            upload_form = RecordingImageUploadForm(request.POST, request.FILES)
            if upload_form.is_valid():
                try:
                    save_images(recording, upload_form.cleaned_data["images"])
                except ValidationError as exc:
                    errors = exc.messages
                except OSError:
                    errors = ["Image storage is unavailable. No images were added."]
            else:
                errors = [str(error) for group in upload_form.errors.values() for error in group]
        elif action == "remove":
            try:
                image = get_object_or_404(RecordingImage, pk=request.POST.get("image_id"), recording=recording)
            except (ValidationError, ValueError):
                raise Http404 from None
            image.delete()
        else:
            errors = ["Choose an image action."]
        recording._prefetched_objects_cache.pop("images", None)
        if not errors:
            if ajax:
                return JsonResponse(gallery_payload(recording, request.user))
            return redirect(request.get_full_path())
    payload = gallery_payload(recording, request.user)
    if ajax:
        return JsonResponse({**payload, "errors": errors}, status=400 if errors else 200)
    return render(request, "lab/recording_images.html", {
        "project": project, "experiment": experiment, "recording": recording,
        "gallery": payload, "upload_form": upload_form, "errors": errors,
        "return_url": safe_return_url(request, experiment_recordings_url(project, experiment)),
    })


@require_safe
def recording_image_file(request, image_id):
    image = get_object_or_404(RecordingImage.objects.select_related("storage_location"), pk=image_id)
    data = read_image(image)
    if data is None:
        raise Http404
    response = HttpResponse(data, content_type="image/webp")
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    if request.GET.get("download") == "1":
        # Do not use user-supplied names in response headers.
        response["Content-Disposition"] = f'attachment; filename="recording-image-{image.pk}.webp"'
    return response
