"""Recording forms and dynamic recording-field helpers."""

import re
from collections import OrderedDict
from datetime import date

from django import forms
from django.db.models import Q

from ..models import (
    Experiment,
    Mouse,
    Project,
    Recording,
    RecordingField,
    RecordingType,
)
from .common import HTML_DATE_FORMAT, normalize_choice_values


def parse_recording_paths(value):
    paths = []

    for line in (value or "").splitlines():
        path = line.strip()

        if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
            path = path[1:-1]

        if len(path) >= 2 and path.startswith('\'') and path.endswith('\''):
            path = path[1:-1]
        if '\'' in path:
            path = path.replace('\'', '')
        if path:
            paths.append(path)

    return paths


def recording_type_choices_for_instance(instance=None):
    queryset = RecordingType.objects.filter(active=True)

    if instance and instance.recording_type_id:
        queryset = RecordingType.objects.filter(
            Q(active=True) | Q(pk=instance.recording_type_id)
        )

    return queryset.order_by("name")


class RecordingDestinationForm(forms.Form):
    recording_type = forms.ModelChoiceField(
        label="Recording type",
        queryset=RecordingType.objects.filter(active=True),
        empty_label="Select a recording type",
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(),
        empty_label="Select a project",
    )
    experiment = forms.ModelChoiceField(
        queryset=Experiment.objects.none(),
        empty_label="Select an experiment",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        experiments = list(
            Experiment.objects.select_related("project")
            .filter(
                archived=False, project__active=True, project__is_service=False,
                recording_type__active=True,
            )
            .order_by("project__name", "project_id", "name", "pk")
        )
        self.destination_options = [
            {
                "recording_type": str(experiment.recording_type_id),
                "project": str(experiment.project_id),
                "project_name": experiment.project.name,
                "experiment": str(experiment.pk),
                "experiment_name": experiment.name,
            }
            for experiment in experiments
        ]
        recording_type_id = str(self["recording_type"].value() or "")
        project_id = str(self["project"].value() or "")
        compatible = [
            experiment for experiment in experiments
            if str(experiment.recording_type_id) == recording_type_id
        ]
        self.fields["project"].queryset = Project.objects.filter(
            pk__in={experiment.project_id for experiment in compatible},
        )
        self.fields["experiment"].queryset = Experiment.objects.filter(
            pk__in=[
                experiment.pk for experiment in compatible
                if str(experiment.project_id) == project_id
            ],
        ).order_by("name", "pk")
        self.fields["experiment"].label_from_instance = lambda experiment: experiment.name


class RecordingForm(forms.Form):
    VALUE_FIELD_PREFIX = "value_"
    GENOTYPING_RECORDING_TYPE_SLUG = "genotyping"
    GENOTYPING_CROSSING_FIELD_KEY = "crossing"

    mice = forms.CharField(
        required=False,
        label="Animals",
        widget=forms.HiddenInput(),
    )
    recording_date = forms.DateField(
        widget=forms.DateInput(
            format=HTML_DATE_FORMAT,
            attrs={"type": "date"},
        ),
        label="Date",
    )
    data_paths = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Recording paths",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Notes",
    )

    def __init__(self, *args, **kwargs):
        self.experiment = kwargs.pop("experiment")
        self.instance = kwargs.pop("instance", None)
        self.source_recording = kwargs.pop("source_recording", None)
        self.owner = kwargs.pop("owner", None)
        selected_mice = kwargs.pop("selected_mice", None)
        selected_mouse = kwargs.pop("selected_mouse", None)
        self.selected_mice = list(selected_mice or [])

        if selected_mouse is not None and selected_mouse not in self.selected_mice:
            self.selected_mice.append(selected_mouse)

        super().__init__(*args, **kwargs)

        self.recording_type = self.experiment.recording_type
        self.dynamic_fields = []
        initial_recording = self.instance or self.source_recording
        self.mouse_queryset = (
            Mouse.objects
            .select_related("crossing_definition")
            .order_by("mouse_id")
        )
        self._pending_mice = []

        initial_mouse_ids = []

        if initial_recording is not None and not self.is_bound:
            mouse_ids = initial_recording.mice.order_by(
                "mouse_id"
            ).values_list(
                "mouse_id",
                flat=True,
            )
            initial_mouse_ids.extend(mouse_ids)
            self.fields["recording_date"].initial = (
                initial_recording.recording_date
            )
            self.fields["data_paths"].initial = "\n".join(
                initial_recording.data_paths
            )
            self.fields["notes"].initial = initial_recording.notes

        if self.selected_mice and not self.is_bound:
            selected_mouse_ids = [
                mouse.mouse_id
                for mouse in self.selected_mice
            ]
            initial_mouse_ids = self.merge_mouse_ids(
                initial_mouse_ids,
                selected_mouse_ids,
            )

        if not self.is_bound:
            self.fields["mice"].initial = "\n".join(initial_mouse_ids)

        if self.recording_type is None:
            return

        for recording_field in (
            self.recording_type.fields
            .filter(active=True)
            .order_by("order", "label")
        ):
            field_name = self.dynamic_field_name(recording_field)
            self.dynamic_fields.append((field_name, recording_field))
            self.fields[field_name] = self.form_field_for_recording_field(
                recording_field
            )

            if self.is_bound:
                continue

            if (
                initial_recording is not None
                and recording_field.key in initial_recording.values
            ):
                initial = initial_recording.values[recording_field.key]
            else:
                initial = recording_field.default_value

            if recording_field.data_type == RecordingField.DATE and initial:
                initial = date.fromisoformat(initial)

            self.fields[field_name].initial = initial

        if self.is_genotyping_recording():
            self.configure_genotyping_crossing_field(initial_mouse_ids)

    def is_genotyping_recording(self):
        return bool(
            self.recording_type
            and self.recording_type.slug == self.GENOTYPING_RECORDING_TYPE_SLUG
        )

    def dynamic_field_name_for_key(self, key):
        for field_name, recording_field in self.dynamic_fields:
            if recording_field.key == key:
                return field_name

        return None

    def configure_genotyping_crossing_field(self, initial_mouse_ids):
        field_name = self.dynamic_field_name_for_key(
            self.GENOTYPING_CROSSING_FIELD_KEY
        )

        if field_name is None:
            return

        field = self.fields[field_name]
        field.required = False
        field.disabled = True
        field.help_text = "Inferred from selected animals."
        field.initial = ""

        if self.is_bound:
            mouse_ids = self.split_mouse_ids(
                self.data.get(self.add_prefix("mice"), "")
            )
        else:
            mouse_ids = initial_mouse_ids

        try:
            field.initial = self.genotyping_crossing_label(
                self.existing_mice_for_ids(mouse_ids)
            )
        except forms.ValidationError:
            field.initial = ""

    def existing_mice_for_ids(self, mouse_ids):
        mice = []
        seen_mouse_ids = set()

        for mouse_id in mouse_ids:
            normalized_mouse_id = mouse_id.lower()

            if normalized_mouse_id in seen_mouse_ids:
                continue

            seen_mouse_ids.add(normalized_mouse_id)
            mouse = self.mouse_queryset.filter(mouse_id__iexact=mouse_id).first()

            if mouse is not None:
                mice.append(mouse)

        return mice

    def genotyping_crossing_key_and_label(self, mouse):
        if mouse.crossing_definition_id:
            return (
                ("structured", mouse.crossing_definition_id),
                mouse.crossing_definition.display_name,
            )

        crossing = (mouse.crossing or "").strip()

        if crossing:
            return (("legacy", crossing.casefold()), crossing)

        return None, ""

    def genotyping_crossing_label(self, mice):
        if not mice:
            raise forms.ValidationError(
                "Select at least one animal to infer the genotyping crossing."
            )

        missing_crossing_mouse_ids = []
        crossing_groups = OrderedDict()

        for mouse in mice:
            crossing_key, crossing_label = self.genotyping_crossing_key_and_label(
                mouse
            )

            if crossing_key is None:
                missing_crossing_mouse_ids.append(mouse.mouse_id)
                continue

            crossing_groups.setdefault(
                crossing_key,
                {
                    "label": crossing_label,
                    "mouse_ids": [],
                },
            )
            crossing_groups[crossing_key]["mouse_ids"].append(mouse.mouse_id)

        if missing_crossing_mouse_ids:
            raise forms.ValidationError(
                "Select animals with a crossing before creating a genotyping "
                "record: "
                f"{', '.join(missing_crossing_mouse_ids)}."
            )

        if len(crossing_groups) > 1:
            crossing_details = [
                f"{', '.join(group['mouse_ids'])} ({group['label']})"
                for group in crossing_groups.values()
            ]
            raise forms.ValidationError(
                "Genotyping records can only include animals from one crossing. "
                "Current selection includes: "
                f"{'; '.join(crossing_details)}."
            )

        return next(iter(crossing_groups.values()))["label"]

    def clean_mice(self):
        mouse_ids = self.split_mouse_ids(self.cleaned_data.get("mice") or "")

        if not mouse_ids:
            return []

        mice = []
        seen_mouse_ids = set()
        missing_mouse_ids = []

        for mouse_id in mouse_ids:
            normalized_mouse_id = mouse_id.lower()

            if normalized_mouse_id in seen_mouse_ids:
                continue

            seen_mouse_ids.add(normalized_mouse_id)
            mouse = self.mouse_queryset.filter(mouse_id__iexact=mouse_id).first()

            if mouse is None:
                missing_mouse_ids.append(mouse_id)
            else:
                mice.append(mouse)

        if missing_mouse_ids:
            raise forms.ValidationError(
                "Select registered animal IDs: "
                f"{', '.join(missing_mouse_ids)}."
            )

        return mice

    def clean_data_paths(self):
        return parse_recording_paths(self.cleaned_data.get("data_paths"))

    def clean(self):
        cleaned_data = super().clean()

        if not self.is_genotyping_recording() or self.errors.get("mice"):
            return cleaned_data

        try:
            crossing_label = self.genotyping_crossing_label(
                cleaned_data.get("mice") or []
            )
        except forms.ValidationError as exc:
            self.add_error("mice", exc)
            return cleaned_data

        crossing_field_name = self.dynamic_field_name_for_key(
            self.GENOTYPING_CROSSING_FIELD_KEY
        )

        if crossing_field_name is not None:
            cleaned_data[crossing_field_name] = crossing_label

        return cleaned_data

    @staticmethod
    def split_mouse_ids(mouse_ids):
        return [
            mouse_id.strip()
            for mouse_id in re.split(r"[,;\r\n]+", mouse_ids)
            if mouse_id.strip()
        ]

    def merge_mouse_ids(self, *mouse_id_groups):
        merged_mouse_ids = []
        seen_mouse_ids = set()

        for mouse_ids in mouse_id_groups:
            for mouse_id in mouse_ids:
                normalized_mouse_id = mouse_id.lower()

                if normalized_mouse_id in seen_mouse_ids:
                    continue

                seen_mouse_ids.add(normalized_mouse_id)
                merged_mouse_ids.append(mouse_id)

        return merged_mouse_ids

    def selected_mouse_ids(self):
        if self.is_bound:
            mice_value = self.data.get(self.add_prefix("mice"), "")
        else:
            mice_value = self.fields["mice"].initial or ""

        return self.split_mouse_ids(mice_value)

    def selected_mice_for_display(self):
        mouse_ids = self.selected_mouse_ids()

        if not mouse_ids:
            return []

        mice = Mouse.objects.filter(pk__in=mouse_ids)
        mice_by_id = {
            mouse.pk: mouse
            for mouse in mice
        }

        return [
            mice_by_id[mouse_id]
            for mouse_id in mouse_ids
            if mouse_id in mice_by_id
        ]

    def dynamic_field_name(self, recording_field):
        return f"{self.VALUE_FIELD_PREFIX}{recording_field.pk}"

    def form_field_for_recording_field(self, recording_field):
        common_kwargs = {
            "required": recording_field.required,
            "label": recording_field.label,
            "help_text": recording_field.units,
        }

        if recording_field.data_type == RecordingField.INTEGER:
            return forms.IntegerField(**common_kwargs)

        if recording_field.data_type == RecordingField.FLOAT:
            return forms.FloatField(**common_kwargs)

        if recording_field.data_type == RecordingField.DATE:
            return forms.DateField(
                widget=forms.DateInput(attrs={"type": "date"}),
                **common_kwargs,
            )

        if recording_field.data_type == RecordingField.BOOLEAN:
            return forms.BooleanField(
                required=False,
                label=recording_field.label,
                help_text=recording_field.units,
            )

        if recording_field.data_type == RecordingField.CHOICE:
            choices = normalize_choice_values(recording_field.choices)

            if not recording_field.required:
                choices = [("", "---------"), *choices]

            return forms.ChoiceField(
                choices=choices,
                **common_kwargs,
            )

        return forms.CharField(
            **common_kwargs,
        )

    def recording_values(self):
        values = {}

        for field_name, recording_field in self.dynamic_fields:
            value = self.cleaned_data.get(field_name)

            if value is None or value == "":
                continue

            if recording_field.data_type == RecordingField.DATE:
                value = value.isoformat()

            values[recording_field.key] = value

        return values

    def save(self, commit=True):
        if self.instance is None:
            recording = Recording(
                experiment=self.experiment,
                recording_type=self.experiment.recording_type,
                owner=self.owner,
            )
        else:
            recording = self.instance
            recording.experiment = self.experiment
            recording.recording_type = self.experiment.recording_type

        mice = self.cleaned_data.get("mice", [])
        recording.recording_date = self.cleaned_data["recording_date"]
        recording.data_paths = self.cleaned_data["data_paths"]
        recording.notes = self.cleaned_data.get("notes", "")
        recording.values = self.recording_values()
        recording.full_clean()
        self._pending_mice = mice

        if commit:
            recording.save()
            recording.mice.set(mice)

        self.instance = recording
        return recording

    def save_m2m(self):
        if self.instance is None or self.instance.pk is None:
            raise ValueError("Save the recording before saving mice.")

        self.instance.mice.set(self._pending_mice)
