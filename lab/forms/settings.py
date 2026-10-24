"""Administrator forms for recording schemas and genotype vocabulary."""
from django import forms

from ..models import GenotypeCategory, RecordingField, RecordingType


class GenotypeCategoryForm(forms.ModelForm):
    class Meta:
        model = GenotypeCategory
        fields = ["label", "code", "position", "active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["code"].disabled = True
            self.fields["code"].help_text = "Stored category codes cannot be changed."


class RecordingTypeForm(forms.ModelForm):
    class Meta:
        model = RecordingType
        fields = ["name", "plural_name", "slug", "description", "active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class RecordingFieldForm(forms.ModelForm):
    class Meta:
        model = RecordingField
        fields = ["key", "label", "data_type", "required", "choices", "units", "default_value", "active", "display_in_table", "filterable", "order"]
        widgets = {"choices": forms.Textarea(attrs={"rows": 3}), "default_value": forms.Textarea(attrs={"rows": 2})}
        help_texts = {
            "choices": 'For choice fields, enter a JSON list, for example ["Low", "High"].',
            "default_value": 'Optional JSON value, for example 0, false, or "text".',
            "active": "Uncheck to retire this field while keeping historical values.",
        }

    def __init__(self, *args, recording_type, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.recording_type = recording_type

    def clean_key(self):
        key = self.cleaned_data["key"]
        if RecordingField.objects.filter(recording_type=self.instance.recording_type, key=key).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This key is already used by another field in this recording type.")
        return key
