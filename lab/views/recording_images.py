"""Authenticated recording attachment management with an HTML fallback."""
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from ..forms.recording_images import RecordingDocumentUploadForm, RecordingImageUploadForm
from ..models import RecordingDocument, RecordingImage
from ..recording_images import (
    available_documents, available_images, read_document, read_image,
    save_documents, save_images,
)
from .common import experiment_for_project_or_404, experiment_recordings_url, safe_return_url
from .recordings import recording_for_experiment_or_404, require_recording_modify_permission


def gallery_payload(recording, user):
    registered = list(recording.images.all())
    available = available_images(recording)
    registered_documents = list(recording.documents.all())
    documents = available_documents(recording)
    return {
        "registered_count": len(registered),
        "unavailable_count": len(registered) - len(available),
        "images": [{
            "id": str(image.pk), "name": image.original_name,
            "width": image.width, "height": image.height,
            "url": reverse("lab:recording_image_file", args=[image.pk]),
            "download_url": reverse("lab:recording_image_file", args=[image.pk]) + "?download=1",
        } for image in available],
        "document_registered_count": len(registered_documents),
        "document_unavailable_count": len(registered_documents) - len(documents),
        "documents": [{
            "id": str(document.pk), "name": document.original_name,
            "size_bytes": document.size_bytes,
            "download_url": reverse("lab:recording_document_file", args=[document.pk]),
        } for document in documents],
        "can_modify": recording.can_be_modified_by(user),
    }


@require_http_methods(["GET", "HEAD", "POST"])
def recording_images(request, project_pk, experiment_pk, recording_pk):
    project, experiment = experiment_for_project_or_404(project_pk, experiment_pk)
    recording = recording_for_experiment_or_404(experiment, recording_pk)
    ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    upload_form = RecordingImageUploadForm()
    document_upload_form = RecordingDocumentUploadForm()
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
        elif action == "upload_documents":
            document_upload_form = RecordingDocumentUploadForm(request.POST, request.FILES)
            if document_upload_form.is_valid():
                try:
                    save_documents(recording, document_upload_form.cleaned_data["documents"])
                except ValidationError as exc:
                    errors = exc.messages
                except OSError:
                    errors = ["Media storage is unavailable. No documents were added."]
            else:
                errors = [str(error) for group in document_upload_form.errors.values() for error in group]
        elif action == "remove":
            try:
                image = get_object_or_404(RecordingImage, pk=request.POST.get("image_id"), recording=recording)
            except (ValidationError, ValueError):
                raise Http404 from None
            image.delete()
        elif action == "remove_document":
            try:
                document = get_object_or_404(
                    RecordingDocument, pk=request.POST.get("document_id"), recording=recording,
                )
            except (ValidationError, ValueError):
                raise Http404 from None
            document.delete()
        else:
            errors = ["Choose an attachment action."]
        recording._prefetched_objects_cache.pop("images", None)
        recording._prefetched_objects_cache.pop("documents", None)
        if not errors:
            if ajax:
                return JsonResponse(gallery_payload(recording, request.user))
            return redirect(request.get_full_path())
    payload = gallery_payload(recording, request.user)
    if ajax:
        return JsonResponse({**payload, "errors": errors}, status=400 if errors else 200)
    return render(request, "lab/recording_images.html", {
        "project": project, "experiment": experiment, "recording": recording,
        "gallery": payload, "upload_form": upload_form,
        "document_upload_form": document_upload_form, "errors": errors,
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


@require_safe
def recording_document_file(request, document_id):
    document = get_object_or_404(
        RecordingDocument.objects.select_related("storage_location"), pk=document_id,
    )
    data = read_document(document)
    if data is None:
        raise Http404
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="recording-document-{document.pk}.pdf"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response
