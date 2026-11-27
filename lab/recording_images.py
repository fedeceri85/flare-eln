"""Private, compressed viewing copies; unavailable storage never hides recordings."""
from io import BytesIO
import logging
from pathlib import Path
import tempfile
from uuid import uuid4
import warnings

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import ImageStorageLocation, RecordingDocument, RecordingImage, SpreadsheetImportImage

logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_BATCH_IMAGES = 10
MAX_DOCUMENT_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_BATCH_DOCUMENTS = 10


def validate_directory(value):
    try:
        directory = Path(value).expanduser()
        if not directory.is_absolute():
            raise ValidationError("Enter an absolute folder path.")
        directory = directory.resolve()
        boundary = settings.RECORDING_MEDIA_BOUNDARY
        if boundary and not directory.is_relative_to(Path(boundary).resolve()):
            raise ValidationError(f"Choose a folder within {boundary}, the persistent media mount.")
        if not directory.is_dir():
            raise ValidationError("Choose an existing folder.")
        # Test actual access under the application account, rather than just mode bits.
        with tempfile.TemporaryFile(dir=directory) as probe:
            probe.write(b"storage check")
            probe.flush()
        return str(directory)
    except (OSError, ValueError) as exc:
        raise ValidationError("The application cannot write to this folder.") from exc


def upload_location():
    directory = Path(settings.RECORDING_MEDIA_ROOT)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValidationError("Media storage is unavailable. Ask an administrator to check the deployment media folder.") from exc
    try:
        directory = validate_directory(str(directory))
    except ValidationError as exc:
        raise ValidationError("Media storage is unavailable. Ask an administrator to check the deployment media folder.") from exc
    return ImageStorageLocation.objects.get_or_create(directory=directory)[0]


def image_path(image):
    return attachment_path(image, "Image")


def document_path(document):
    return attachment_path(document, "Document")


def attachment_path(attachment, label):
    root = Path(attachment.storage_location.directory).resolve()
    path = (root / attachment.filename).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"{label} path is outside its storage folder.")
    return path


def image_status(image):
    """Only gallery/file/audit paths inspect storage; table counts use metadata."""
    try:
        with image_path(image).open("rb") as source:
            header = source.read(12)
        return "available" if header[:4] == b"RIFF" and header[8:12] == b"WEBP" else "invalid format"
    except FileNotFoundError:
        return "missing"
    except ValueError:
        return "invalid path"
    except OSError:
        return "unreadable"


def image_available(image):
    return image_status(image) == "available"


def available_images(recording):
    cached = getattr(recording, "_prefetched_objects_cache", {}).get("images")
    images = cached if cached is not None else recording.images.select_related("storage_location")
    return [image for image in images if image_available(image)]


def read_image(image):
    """Read before sending headers, so even late file-read errors become a normal 404."""
    try:
        with image_path(image).open("rb") as source:
            data = source.read(MAX_UPLOAD_BYTES + 1)
        return data if data and len(data) <= MAX_UPLOAD_BYTES else None
    except (OSError, ValueError):
        return None


def document_status(document):
    try:
        path = document_path(document)
        size = path.stat().st_size
        if not 0 < size <= MAX_DOCUMENT_UPLOAD_BYTES:
            return "invalid size"
        with path.open("rb") as source:
            header = source.read(1024)
        return "available" if b"%PDF-" in header else "invalid format"
    except FileNotFoundError:
        return "missing"
    except ValueError:
        return "invalid path"
    except OSError:
        return "unreadable"


def document_available(document):
    return document_status(document) == "available"


def available_documents(recording):
    cached = getattr(recording, "_prefetched_objects_cache", {}).get("documents")
    documents = cached if cached is not None else recording.documents.select_related("storage_location")
    return [document for document in documents if document_available(document)]


def read_document(document):
    """Read a validated PDF fully so storage errors become a normal 404."""
    try:
        with document_path(document).open("rb") as source:
            data = source.read(MAX_DOCUMENT_UPLOAD_BYTES + 1)
        return data if data and len(data) <= MAX_DOCUMENT_UPLOAD_BYTES and b"%PDF-" in data[:1024] else None
    except (OSError, ValueError):
        return None


def compress_image(upload):
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError(f"{upload.name}: choose an image smaller than 20 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(upload) as source:
                if source.format not in {"JPEG", "PNG", "WEBP", "TIFF"} or (source.format != "TIFF" and getattr(source, "is_animated", False)):
                    raise ValidationError(f"{upload.name}: choose a JPEG, PNG, WebP or TIFF image.")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise ValidationError(f"{upload.name}: choose an image with no more than 40 million pixels.")
                # TIFF attachments use the first page as their viewing copy.
                source.seek(0)
                source.load()
                image = ImageOps.exif_transpose(source)
                image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
                output = BytesIO()
                image.save(output, format="WEBP", quality=85, method=6)
                return output.getvalue(), image.width, image.height
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError(f"{upload.name}: this image could not be read.") from exc


def save_images(recording, uploads):
    return _save_image_batch(RecordingImage, {"recording": recording}, uploads)


def validate_document(upload):
    if upload.size > MAX_DOCUMENT_UPLOAD_BYTES:
        raise ValidationError(f"{upload.name}: choose a PDF no larger than 10 MB.")
    if Path(upload.name).suffix.lower() != ".pdf":
        raise ValidationError(f"{upload.name}: choose a PDF document.")
    try:
        upload.seek(0)
        data = upload.read(MAX_DOCUMENT_UPLOAD_BYTES + 1)
        upload.seek(0)
    except (OSError, ValueError) as exc:
        raise ValidationError(f"{upload.name}: this PDF could not be read.") from exc
    if not data or len(data) > MAX_DOCUMENT_UPLOAD_BYTES or b"%PDF-" not in data[:1024]:
        raise ValidationError(f"{upload.name}: this file is not a valid PDF document.")
    return data


def save_documents(recording, uploads):
    if not uploads:
        raise ValidationError("Choose at least one PDF document.")
    if len(uploads) > MAX_BATCH_DOCUMENTS:
        raise ValidationError("Choose up to 10 PDF documents at a time.")
    processed = [(upload, validate_document(upload)) for upload in uploads]
    location = upload_location()
    written, saved = [], []
    try:
        with transaction.atomic():
            for upload, data in processed:
                filename = f"{uuid4().hex}.pdf"
                path = Path(location.directory) / filename
                with path.open("xb") as target:
                    written.append(path)
                    target.write(data)
                saved.append(RecordingDocument.objects.create(
                    recording=recording,
                    storage_location=location,
                    filename=filename,
                    original_name=Path(upload.name).name[:255],
                    size_bytes=len(data),
                ))
    except Exception:
        for path in written:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not clean up recording document %s", path)
        raise
    return saved


def save_import_images(draft, uploads):
    if draft.shared_images.count() + len(uploads) > MAX_BATCH_IMAGES:
        raise ValidationError("Attach up to 10 shared images per import.")
    return _save_image_batch(SpreadsheetImportImage, {"draft": draft}, uploads)


def _save_image_batch(model, parent, uploads):
    if not uploads:
        raise ValidationError("Choose at least one image.")
    if len(uploads) > MAX_BATCH_IMAGES:
        raise ValidationError("Choose up to 10 images at a time.")
    # Validate the whole batch before writing anything.
    processed = [(upload, *compress_image(upload)) for upload in uploads]
    location = upload_location()
    written, saved = [], []
    try:
        with transaction.atomic():
            for upload, data, width, height in processed:
                filename = f"{uuid4().hex}.webp"
                path = Path(location.directory) / filename
                with path.open("xb") as target:
                    written.append(path)
                    target.write(data)
                saved.append(model.objects.create(
                    **parent, storage_location=location, filename=filename,
                    original_name=Path(upload.name).name[:255],
                    width=width, height=height, size_bytes=len(data),
                ))
    except Exception:
        for path in written:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not clean up recording image %s", path)
        raise
    return saved


def delete_image_file(image):
    # A shared import stores one file with independent links from each recording.
    references = {"storage_location_id": image.storage_location_id, "filename": image.filename}
    if RecordingImage.objects.filter(**references).exists() or SpreadsheetImportImage.objects.filter(**references).exists():
        return
    discard_image_files([image])


def delete_document_file(document):
    discard_document_files([document])


def discard_image_files(images):
    """Remove newly written files after an enclosing database transaction fails."""
    for image in images:
        _unlink_image_file(image)


def discard_document_files(documents):
    """Remove newly written PDF files after an enclosing transaction fails."""
    for document in documents:
        try:
            document_path(document).unlink(missing_ok=True)
        except (OSError, ValueError):
            logger.info("Recording document file unavailable during removal: %s", document.pk)


def _unlink_image_file(image):
    try:
        image_path(image).unlink(missing_ok=True)
    except (OSError, ValueError):
        # A missing/unmounted/read-only location must never prevent record deletion.
        logger.info("Recording image file unavailable during removal: %s", image.pk)
