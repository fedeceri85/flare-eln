"""Names are the only editable template metadata in Settings."""
from django import forms

from ..models import SpreadsheetImportTemplate


class ImportTemplateNameForm(forms.ModelForm):
    class Meta:
        model = SpreadsheetImportTemplate
        fields = ["name"]
        labels = {"name": "Template name"}

    def __init__(self, *args, recording_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        if recording_type is not None:
            self.instance.recording_type = recording_type

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        existing = SpreadsheetImportTemplate.objects.filter(
            recording_type=self.instance.recording_type, name__iexact=name,
        ).exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError("A template with this name already exists for this experiment type.")
        return name
