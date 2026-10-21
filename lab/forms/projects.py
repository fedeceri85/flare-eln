"""Project and experiment forms."""

from django import forms
from django.db.models import Q

from ..models import Experiment, Project
from .recordings import recording_type_choices_for_instance


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = [
            "name",
            "lab_member",
            "description",
            "start_date",
            "end_date",
            "active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if start_date and end_date and start_date > end_date:
            self.add_error(
                "end_date",
                "End date must be on or after the start date.",
            )

        return cleaned_data


class ExperimentForm(forms.ModelForm):
    recording_type_query = forms.CharField(
        label="Experiment type",
        required=True,
        widget=forms.TextInput(
            attrs={
                "list": "recording-type-options",
                "autocomplete": "off",
                "placeholder": "Type to search recording types",
            }
        ),
    )

    class Meta:
        model = Experiment
        fields = [
            "name",
            "description",
            "start_date",
            "end_date",
            "archived",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.recording_type_queryset = recording_type_choices_for_instance(
            self.instance
        )
        self.recording_type_options = list(self.recording_type_queryset)
        self.recording_type_locked = (
            bool(self.instance.pk)
            and self.instance.recordings.exists()
        )

        if self.instance.recording_type_id:
            self.fields["recording_type_query"].initial = str(
                self.instance.recording_type
            )

        if self.recording_type_locked:
            self.fields["recording_type_query"].disabled = True
            self.fields["recording_type_query"].help_text = (
                "Experiment type cannot be changed after recordings "
                "have been added."
            )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if start_date and end_date and start_date > end_date:
            self.add_error(
                "end_date",
                "End date must be on or after the start date.",
            )

        if self.recording_type_locked:
            cleaned_data["recording_type"] = self.instance.recording_type
            return cleaned_data

        recording_type_query = (
            cleaned_data.get("recording_type_query") or ""
        ).strip()

        if not recording_type_query:
            self.add_error(
                "recording_type_query",
                "Select an experiment type.",
            )
            return cleaned_data

        exact_match = self.recording_type_queryset.filter(
            Q(name__iexact=recording_type_query)
            | Q(slug__iexact=recording_type_query)
        ).first()

        if exact_match:
            cleaned_data["recording_type"] = exact_match
            return cleaned_data

        matches = list(
            self.recording_type_queryset.filter(
                Q(name__icontains=recording_type_query)
                | Q(slug__icontains=recording_type_query)
            )[:2]
        )

        if len(matches) == 1:
            cleaned_data["recording_type"] = matches[0]
        elif not matches:
            self.add_error(
                "recording_type_query",
                "Select a registered active experiment type.",
            )
        else:
            self.add_error(
                "recording_type_query",
                "Keep typing until exactly one experiment type matches.",
            )

        return cleaned_data

    def save(self, commit=True):
        experiment = super().save(commit=False)
        experiment.recording_type = self.cleaned_data["recording_type"]

        if commit:
            experiment.save()
            self.save_m2m()

        return experiment
