"""Read-only filesystem audit. Never repair or delete attachment references."""
from django.core.management.base import BaseCommand
from lab.models import RecordingImage
from lab.recording_images import image_status


class Command(BaseCommand):
    help = "Report missing, unreadable or invalid registered recording images without changing files or records."

    def handle(self, *args, **options):
        checked = unavailable = 0
        for image in RecordingImage.objects.select_related('storage_location').order_by('pk').iterator(chunk_size=500):
            checked += 1
            status = image_status(image)
            if status != "available":
                unavailable += 1
                self.stdout.write(f"Unavailable attachment {image.pk} (recording {image.recording_id}, {status}): {image.filename}")
        self.stdout.write(f"Checked {checked} registered attachments; {unavailable} unavailable. No changes made.")
