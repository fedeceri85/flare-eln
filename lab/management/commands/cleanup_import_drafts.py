from django.core.management.base import BaseCommand
from django.utils import timezone

from lab.models import SpreadsheetImportDraft


class Command(BaseCommand):
    help = "Delete spreadsheet import drafts older than their 24-hour expiry."

    def handle(self, *args, **options):
        count, _ = SpreadsheetImportDraft.objects.filter(expires_at__lte=timezone.now()).delete()
        self.stdout.write(f"Deleted {count} expired import drafts.")
