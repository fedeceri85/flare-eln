import re
from collections import OrderedDict
from datetime import date

from django import forms
from django.db.models import Q
from django.utils import timezone

from .models import (
    Crossing,
    Experiment,
    MAX_CROSSING_LINES,
    Mouse,
    MouseGenotype,
    MouseLine,
    Procedure,
    ProcedureType,
    Project,
    Protocol,
    Recording,
    RecordingField,
    RecordingType,
    MAX_SEVERITY_CHOICES,
    Species,
    ZYGOSITY_CHOICES,
    ZYGOSITY_LABELS,
    resolve_crossing,
)


HTML_DATE_FORMAT = "%Y-%m-%d"
DATE_WIDGETS = {
    "date_of_birth": forms.DateInput(
        format=HTML_DATE_FORMAT,
        attrs={"type": "date"},
    ),
    "protocol_start_date": forms.DateInput(
        format=HTML_DATE_FORMAT,
        attrs={"type": "date"},
    ),
    "cull_date": forms.DateInput(
        format=HTML_DATE_FORMAT,
        attrs={"type": "date"},
    ),
}
ENGLISH_DATE_FORMAT = "%d/%m/%Y"
HOME_OFFICE_DATE_INPUT_FORMATS = [
    ENGLISH_DATE_FORMAT,
    "%Y-%m-%d",
]
LOCAL_IDENTIFIER_LABEL = "Local identifier"
LOCAL_IDENTIFIER_HELP_TEXT = (
    "Tattoo, ear clip, experiment mouse number, or other local ID."
)


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


MOUSE_EDIT_FIELDS = [
    "date_of_birth",
    "tattoo",
    "breeding_pair",
    "crossing_definition",
    "sex",
    "status",
    "protocol_start_date",
    "cull_date",
    "protocol",
    "severity",
    "notes",
]


class HomeOfficeReturnForm(forms.Form):
    OUTPUT_MODE_CHOICES = [
        ("table", "Table"),
        ("text", "Text"),
    ]

    licence_reference = forms.ChoiceField(
        label="Project licence",
    )
    start_date = forms.DateField(
        input_formats=HOME_OFFICE_DATE_INPUT_FORMATS,
        widget=forms.DateInput(
            format=ENGLISH_DATE_FORMAT,
            attrs={
                "autocomplete": "off",
                "inputmode": "numeric",
                "placeholder": "dd/mm/yyyy",
            },
        ),
        label="Start date",
    )
    end_date = forms.DateField(
        input_formats=HOME_OFFICE_DATE_INPUT_FORMATS,
        widget=forms.DateInput(
            format=ENGLISH_DATE_FORMAT,
            attrs={
                "autocomplete": "off",
                "inputmode": "numeric",
                "placeholder": "dd/mm/yyyy",
            },
        ),
        label="End date",
    )
    output_mode = forms.ChoiceField(
        choices=OUTPUT_MODE_CHOICES,
        initial="table",
        label="Output mode",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        licence_references = (
            Protocol.objects
            .filter(allows_regulated_procedures=True)
            .order_by("licence_reference")
            .values_list("licence_reference", flat=True)
            .distinct()
        )

        self.fields["licence_reference"].choices = [
            (licence_reference, licence_reference)
            for licence_reference in licence_references
        ]
        self.fields["start_date"].initial = date(today.year, 1, 1)
        self.fields["end_date"].initial = today

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


def recording_type_choices_for_instance(instance=None):
    queryset = RecordingType.objects.filter(active=True)

    if instance and instance.recording_type_id:
        queryset = RecordingType.objects.filter(
            Q(active=True) | Q(pk=instance.recording_type_id)
        )

    return queryset.order_by("name")


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


class RecordingForm(forms.Form):
    VALUE_FIELD_PREFIX = "value_"
    GENOTYPING_RECORDING_TYPE_SLUG = "genotyping"
    GENOTYPING_CROSSING_FIELD_KEY = "crossing"

    mice = forms.CharField(
        required=False,
        label="Mice",
        widget=forms.HiddenInput(),
    )
    recording_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Date",
    )
    sequence_number = forms.IntegerField(
        min_value=1,
        initial=1,
        label="Sequence",
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
            self.fields["sequence_number"].initial = (
                initial_recording.sequence_number
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
        field.help_text = "Inferred from selected mice."
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
                "Select at least one mouse to infer the genotyping crossing."
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
                "Select mice with a crossing before creating a genotyping "
                "record: "
                f"{', '.join(missing_crossing_mouse_ids)}."
            )

        if len(crossing_groups) > 1:
            crossing_details = [
                f"{', '.join(group['mouse_ids'])} ({group['label']})"
                for group in crossing_groups.values()
            ]
            raise forms.ValidationError(
                "Genotyping records can only include mice from one crossing. "
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
                "Select registered mouse IDs: "
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
        recording.sequence_number = self.cleaned_data["sequence_number"]
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


def normalize_choice_values(choices):
    normalized_choices = []

    for choice in choices or []:
        if isinstance(choice, dict):
            value = choice.get("value", "")
            label = choice.get("label", value)
        elif isinstance(choice, (list, tuple)) and len(choice) >= 2:
            value, label = choice[0], choice[1]
        else:
            value = choice
            label = choice

        normalized_choices.append((value, label))

    return normalized_choices


def active_crossings_queryset():
    return (
        Crossing.objects
        .filter(active=True)
        .select_related("species")
        .prefetch_related("lines")
        .order_by("species__name", "display_name", "crossing_id")
    )


def crossing_choice_label(crossing):
    return f"{crossing.species.name} - {crossing.display_name}"


def genotype_field_name(mouse_line):
    line_id = mouse_line.pk if hasattr(mouse_line, "pk") else mouse_line
    return f"genotype_line_{line_id}"


def truncate_compat_text(value, max_length=100):
    if len(value) <= max_length:
        return value

    return value[: max_length - 3].rstrip() + "..."


def genotype_summary(genotype_calls):
    return "; ".join(
        f"{line.name}: {ZYGOSITY_LABELS.get(zygosity, zygosity)}"
        for line, zygosity in genotype_calls
    )


def visible_field_sections_for_names(form, field_names, genotype_field_names):
    sections = []
    regular_fields = []
    genotype_fields = []
    genotype_field_names = set(genotype_field_names)

    for field_name in field_names:
        if field_name not in form.fields:
            continue

        field = form[field_name]

        if field.is_hidden:
            continue

        if field.name in genotype_field_names:
            if regular_fields:
                sections.append({"fields": regular_fields})
                regular_fields = []

            genotype_fields.append(field)
            continue

        if genotype_fields:
            sections.append(
                {
                    "title": "Genotype",
                    "class": "genotype-subset",
                    "fields": genotype_fields,
                }
            )
            genotype_fields = []

        regular_fields.append(field)

    if regular_fields:
        sections.append({"fields": regular_fields})

    if genotype_fields:
        sections.append(
            {
                "title": "Genotype",
                "class": "genotype-subset",
                "fields": genotype_fields,
            }
        )

    return sections


def sync_mouse_structured_genetics(
    mouse,
    crossing,
    genotype_values,
    preserve_missing=False,
):
    line_ids = set(crossing.lines.values_list("pk", flat=True))

    mouse.crossing_definition = crossing
    mouse.crossing = truncate_compat_text(crossing.display_name)
    mouse.crossing_type = crossing.inferred_crossing_type

    lines = list(crossing.lines.order_by("name", "mouse_line_id"))
    existing_calls = {}

    if preserve_missing:
        existing_calls = {
            genotype.mouse_line_id: genotype.zygosity
            for genotype in mouse.genotype_calls.filter(mouse_line_id__in=line_ids)
        }

    summary_values = []

    for line in lines:
        zygosity = genotype_values.get(line.pk)

        if zygosity is None and preserve_missing:
            zygosity = existing_calls.get(line.pk)

        zygosity = zygosity or "unknown"
        MouseGenotype.objects.update_or_create(
            mouse=mouse,
            mouse_line=line,
            defaults={"zygosity": zygosity},
        )
        summary_values.append((line, zygosity))

    MouseGenotype.objects.filter(mouse=mouse).exclude(
        mouse_line_id__in=line_ids,
    ).delete()

    mouse.genotype = truncate_compat_text(genotype_summary(summary_values))
    mouse.save(
        update_fields=[
            "crossing_definition",
            "crossing",
            "crossing_type",
            "genotype",
        ]
    )


class BulkMouseLineCreateForm(forms.Form):
    species = forms.ModelChoiceField(
        queryset=Species.objects.filter(active=True).order_by("name"),
        label="Species",
    )
    line_names = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Line names",
        help_text="Enter one or more line names separated by commas.",
    )
    is_wild_type = forms.BooleanField(
        required=False,
        label="These are wild-type/reference lines",
    )

    def clean_line_names(self):
        raw_names = self.cleaned_data["line_names"]
        names = []
        seen_names = set()

        for name in raw_names.split(","):
            name = name.strip()

            if not name:
                continue

            normalized_name = name.casefold()

            if normalized_name in seen_names:
                continue

            seen_names.add(normalized_name)
            names.append(name)

        if not names:
            raise forms.ValidationError("Enter at least one line name.")

        return names

    def save(self):
        species = self.cleaned_data["species"]
        is_wild_type = self.cleaned_data["is_wild_type"]
        created_lines = []
        existing_lines = []

        for name in self.cleaned_data["line_names"]:
            existing_line = (
                MouseLine.objects
                .filter(species=species, name__iexact=name)
                .first()
            )

            if existing_line is not None:
                existing_lines.append(existing_line)
                continue

            created_lines.append(
                MouseLine.objects.create(
                    species=species,
                    name=name,
                    is_wild_type=is_wild_type,
                )
            )

        return created_lines, existing_lines


class CrossingCreateForm(forms.Form):
    species = forms.ModelChoiceField(
        queryset=Species.objects.filter(active=True).order_by("name"),
        label="Species",
    )
    selected_lines = forms.CharField(
        widget=forms.HiddenInput(),
        label="Selected lines",
    )

    def clean_selected_lines(self):
        raw_line_ids = [
            line_id.strip()
            for line_id in self.cleaned_data["selected_lines"].split(",")
            if line_id.strip()
        ]
        line_ids = []
        seen_line_ids = set()

        for raw_line_id in raw_line_ids:
            try:
                line_id = int(raw_line_id)
            except ValueError as exc:
                raise forms.ValidationError("Select valid lines.") from exc

            if line_id in seen_line_ids:
                continue

            seen_line_ids.add(line_id)
            line_ids.append(line_id)

        if not line_ids:
            raise forms.ValidationError("Select at least one line.")

        if len(line_ids) > MAX_CROSSING_LINES:
            raise forms.ValidationError(
                f"Select at most {MAX_CROSSING_LINES} lines."
            )

        return line_ids

    def clean(self):
        cleaned_data = super().clean()
        species = cleaned_data.get("species")
        line_ids = cleaned_data.get("selected_lines")

        if species is None or not line_ids:
            return cleaned_data

        lines = list(
            MouseLine.objects
            .filter(pk__in=line_ids, active=True)
            .select_related("species")
        )
        lines_by_id = {
            line.pk: line
            for line in lines
        }
        missing_line_ids = [
            str(line_id)
            for line_id in line_ids
            if line_id not in lines_by_id
        ]

        if missing_line_ids:
            self.add_error("selected_lines", "Select active lines.")
            return cleaned_data

        mismatched_lines = [
            line.name
            for line in lines
            if line.species_id != species.pk
        ]

        if mismatched_lines:
            self.add_error(
                "selected_lines",
                f"All selected lines must belong to {species.name}.",
            )
            return cleaned_data

        cleaned_data["lines"] = [
            lines_by_id[line_id]
            for line_id in line_ids
        ]
        return cleaned_data

    def save(self):
        return resolve_crossing(
            self.cleaned_data["species"],
            self.cleaned_data["lines"],
        )


class MouseStructuredGeneticsMixin:
    def configure_genetics_fields(self):
        self.insert_crossing_search_field()
        self.fields["crossing_definition"].required = False
        self.fields["crossing_definition"].queryset = active_crossings_queryset()
        self.fields["crossing_definition"].widget = forms.HiddenInput(
            attrs={"data-crossing-definition-input": "true"}
        )
        self.genotype_fields = []
        self.preserve_legacy_genetics = False
        self.selected_crossing = self.selected_crossing_for_fields()

        if not self.is_bound:
            if self.selected_crossing is not None:
                self.fields["crossing_search"].initial = crossing_choice_label(
                    self.selected_crossing
                )
            elif self.instance.pk and self.instance.crossing:
                self.fields["crossing_search"].initial = ""
                self.preserve_legacy_genetics = True

        if self.selected_crossing is None:
            return

        existing_calls = {}

        if self.instance.pk:
            existing_calls = {
                genotype.mouse_line_id: genotype.zygosity
                for genotype in self.instance.genotype_calls.all()
            }

        genotype_form_fields = OrderedDict()

        for line in self.selected_crossing.lines.order_by("name", "mouse_line_id"):
            field_name = genotype_field_name(line)
            self.genotype_fields.append(field_name)
            genotype_form_fields[field_name] = forms.ChoiceField(
                required=False,
                choices=ZYGOSITY_CHOICES,
                initial=existing_calls.get(line.pk, "unknown"),
                label=line.name,
                widget=forms.Select(
                    attrs={
                        "data-genotype-field": "true",
                        "data-line-id": line.pk,
                    }
                ),
            )

        self.insert_genotype_fields(genotype_form_fields)

    def selected_crossing_for_fields(self):
        if self.is_bound:
            raw_crossing_id = self.data.get(
                self.add_prefix("crossing_definition"),
                "",
            )

            if raw_crossing_id:
                try:
                    return active_crossings_queryset().get(pk=raw_crossing_id)
                except (Crossing.DoesNotExist, ValueError):
                    return None

            return None

        if self.instance.pk and self.instance.crossing_definition_id:
            return self.instance.crossing_definition

        initial_crossing = self.initial.get("crossing_definition")

        if isinstance(initial_crossing, Crossing):
            return initial_crossing

        if initial_crossing:
            try:
                return active_crossings_queryset().get(pk=initial_crossing)
            except (Crossing.DoesNotExist, ValueError):
                return None

        return None

    def insert_crossing_search_field(self):
        crossing_search = forms.CharField(
            required=False,
            label="Crossing",
            widget=forms.TextInput(
                attrs={
                    "autocomplete": "off",
                    "data-crossing-lookup-input": "true",
                    "placeholder": "Type to search crossings",
                }
            ),
        )

        reordered_fields = OrderedDict()
        inserted = False

        for field_name, field in self.fields.items():
            reordered_fields[field_name] = field

            if field_name == "breeding_pair":
                reordered_fields["crossing_search"] = crossing_search
                inserted = True

        if not inserted:
            reordered_fields["crossing_search"] = crossing_search

        self.fields = reordered_fields

    def insert_genotype_fields(self, genotype_form_fields):
        reordered_fields = OrderedDict()
        inserted = False

        for field_name, field in self.fields.items():
            if field_name in genotype_form_fields:
                continue

            reordered_fields[field_name] = field

            if field_name == "crossing_search":
                reordered_fields.update(genotype_form_fields)
                inserted = True

        if not inserted:
            reordered_fields.update(genotype_form_fields)

        self.fields = reordered_fields

    @property
    def visible_field_sections(self):
        if not self.genotype_fields:
            return None

        sections = []
        regular_fields = []
        genotype_fields = []
        genotype_field_names = set(self.genotype_fields)

        for field in self.visible_fields():
            if field.name in genotype_field_names:
                if regular_fields:
                    sections.append({"fields": regular_fields})
                    regular_fields = []

                genotype_fields.append(field)
                continue

            if genotype_fields:
                sections.append(
                    {
                        "title": "Genotype",
                        "class": "genotype-subset",
                        "fields": genotype_fields,
                    }
                )
                genotype_fields = []

            regular_fields.append(field)

        if regular_fields:
            sections.append({"fields": regular_fields})

        if genotype_fields:
            sections.append(
                {
                    "title": "Genotype",
                    "class": "genotype-subset",
                    "fields": genotype_fields,
                }
            )

        return sections

    def clean_crossing_definition(self):
        crossing = self.cleaned_data.get("crossing_definition")
        crossing_search = (self.cleaned_data.get("crossing_search") or "").strip()

        if crossing is not None:
            return crossing

        if crossing_search:
            matching_crossings = [
                candidate
                for candidate in active_crossings_queryset()
                if crossing_choice_label(candidate).casefold()
                == crossing_search.casefold()
            ]

            if len(matching_crossings) == 1:
                return matching_crossings[0]

            raise forms.ValidationError("Select an existing crossing.")

        if self.instance.pk and self.instance.crossing_definition_id is None:
            self.preserve_legacy_genetics = True
            return None

        raise forms.ValidationError("Select a crossing.")

    def clean(self):
        cleaned_data = super().clean()
        crossing = cleaned_data.get("crossing_definition")

        if crossing is None:
            return cleaned_data

        genotype_values = {}
        existing_calls = {}

        if self.instance.pk:
            existing_calls = {
                genotype.mouse_line_id: genotype.zygosity
                for genotype in self.instance.genotype_calls.all()
            }

        for line in crossing.lines.order_by("name", "mouse_line_id"):
            field_name = genotype_field_name(line)
            zygosity = cleaned_data.get(field_name)

            if not zygosity:
                zygosity = existing_calls.get(line.pk, "unknown")

            genotype_values[line.pk] = zygosity

        cleaned_data["genotype_values"] = genotype_values
        return cleaned_data

    def save_structured_genetics(self, mouse):
        if self.preserve_legacy_genetics:
            return

        crossing = self.cleaned_data.get("crossing_definition")

        if crossing is None:
            return

        sync_mouse_structured_genetics(
            mouse,
            crossing,
            self.cleaned_data.get("genotype_values", {}),
        )

    @property
    def legacy_genetics_summary(self):
        if (
            self.instance.pk
            and self.instance.crossing_definition_id is None
            and (
                self.instance.crossing
                or self.instance.crossing_type
                or self.instance.genotype
            )
        ):
            return {
                "crossing": self.instance.crossing,
                "crossing_type": self.instance.get_crossing_type_display(),
                "genotype": self.instance.genotype,
            }

        return None


class MouseForm(MouseStructuredGeneticsMixin, forms.ModelForm):
    generate_mouse_id = forms.BooleanField(
        required=False,
        initial=True,
        label="Generate mouse ID automatically",
    )

    class Meta:
        model = Mouse
        fields = [
            "generate_mouse_id",
            "mouse_id",
            "date_of_birth",
            "tattoo",
            "breeding_pair",
            "crossing_definition",
            "sex",
            "status",
            "protocol_start_date",
            "cull_date",
            "custom_metadata",
            "protocol",
            "severity",
            "notes"
        ]

        widgets = {
            **DATE_WIDGETS,
            "custom_metadata": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": '{"key": "value"}',
                }
            ),
        }
        labels = {
            "tattoo": LOCAL_IDENTIFIER_LABEL,
        }
        help_texts = {
            "tattoo": LOCAL_IDENTIFIER_HELP_TEXT,
        }

    def __init__(self, *args, **kwargs):
        selected_protocol = kwargs.pop("selected_protocol", None)
        super().__init__(*args, **kwargs)
        self.fields["mouse_id"].required = False
        self.fields["protocol"].required = True
        self.fields["protocol"].empty_label = "Select a protocol"
        self.fields["protocol"].queryset = Protocol.objects.order_by(
            "licence_reference",
            "protocol_number",
            "protocol_id",
        )

        if self.instance.pk:
            self.fields["generate_mouse_id"].initial = False
            self.fields["protocol"].empty_label = None

        if selected_protocol is not None:
            self.fields["protocol"].initial = selected_protocol
            self.fields["protocol"].empty_label = None
            self.fields["protocol"].disabled = True

        self.configure_genetics_fields()

    def clean_protocol(self):
        protocol = self.cleaned_data.get("protocol")

        if protocol is None:
            raise forms.ValidationError("Select a protocol.")

        return protocol

    def clean(self):
        cleaned_data = super().clean()
        generate_mouse_id = cleaned_data.get("generate_mouse_id")

        if generate_mouse_id:
            for field_name in ("date_of_birth", "breeding_pair", "tattoo"):
                if not cleaned_data.get(field_name):
                    self.add_error(
                        field_name,
                        "Required when generating the mouse ID automatically.",
                    )
        elif not cleaned_data.get("mouse_id"):
            self.add_error(
                "mouse_id",
                "Enter a mouse ID or enable automatic generation.",
            )

        return cleaned_data


class LitterCreateForm(MouseStructuredGeneticsMixin, forms.Form):
    ID_FIELDS = [
        "mouse_count",
        "tattoo_start",
        "use_progressive_mouse_ids",
        "mouse_id_prefix",
        "mouse_id_start",
    ]
    MOUSE_FIELDS = [
        "date_of_birth",
        "breeding_pair",
        "crossing_search",
        "sex",
        "status",
        "protocol",
        "protocol_start_date",
        "cull_date",
        "severity",
        "notes",
    ]
    PROCEDURE_FIELDS = [
        "procedure_type",
        "procedure_date",
        "procedure_lab_member",
        "procedure_lab_book_page",
    ]

    mouse_count = forms.IntegerField(
        min_value=1,
        max_value=500,
        initial=1,
        label="Number of mice",
    )
    tattoo_start = forms.IntegerField(
        min_value=1,
        label="Local identifier starting number",
        help_text="Used as the local identifier for each mouse.",
    )
    breeding_pair = forms.CharField(
        required=False,
        max_length=100,
        label="Breeding pair",
        help_text="Required when mouse IDs are generated automatically.",
    )
    use_progressive_mouse_ids = forms.BooleanField(
        required=False,
        label="Use progressive mouse IDs",
    )
    mouse_id_prefix = forms.CharField(
        required=False,
        max_length=80,
        label="Mouse ID prefix",
        help_text="When progressive IDs are used, IDs are prefix plus number.",
    )
    mouse_id_start = forms.IntegerField(
        required=False,
        min_value=1,
        label="Mouse ID starting number",
        help_text="Leave blank to start from the local identifier number.",
    )

    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Date of birth",
        help_text="Required when mouse IDs are generated automatically.",
    )
    crossing_definition = forms.ModelChoiceField(
        required=False,
        queryset=Crossing.objects.none(),
        label="Crossing",
    )
    sex = forms.ChoiceField(
        required=False,
        choices=[("", "---------"), *Mouse.SEX_CHOICES],
        label="Sex",
    )
    status = forms.CharField(
        required=False,
        max_length=50,
        initial="Alive",
        label="Status",
    )
    protocol = forms.ModelChoiceField(
        required=True,
        queryset=Protocol.objects.all().order_by(
            "licence_reference",
            "protocol_number",
        ),
        label="Protocol",
    )
    protocol_start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Protocol start date",
    )
    cull_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Cull date",
    )
    severity = forms.ChoiceField(
        required=False,
        choices=[("", "---------"), *MAX_SEVERITY_CHOICES],
        label="Max severity",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Notes",
    )

    procedure_type = forms.ModelChoiceField(
        required=False,
        queryset=ProcedureType.objects.all().order_by("name"),
        label="Procedure type to add",
        help_text="Selecting a procedure creates one procedure row for each mouse.",
    )
    procedure_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Date",
    )
    procedure_lab_member = forms.CharField(
        required=False,
        max_length=100,
        label="Lab member",
    )
    procedure_lab_book_page = forms.CharField(
        required=False,
        max_length=50,
        label="Lab-book page",
    )

    def clean_mouse_id_prefix(self):
        return (self.cleaned_data.get("mouse_id_prefix") or "").strip()

    def __init__(self, *args, **kwargs):
        selected_protocol = kwargs.pop("selected_protocol", None)
        self.instance = Mouse()
        super().__init__(*args, **kwargs)

        self.fields["protocol"].required = True
        self.fields["protocol"].empty_label = "Select a protocol"
        self.fields["protocol"].queryset = Protocol.objects.all().order_by(
            "licence_reference",
            "protocol_number",
        )

        if selected_protocol is not None:
            self.fields["protocol"].initial = selected_protocol
            self.fields["protocol"].empty_label = None
            self.fields["protocol"].disabled = True

        self.configure_genetics_fields()

    def clean(self):
        cleaned_data = super().clean()
        use_progressive_mouse_ids = self.uses_progressive_mouse_ids()

        if use_progressive_mouse_ids:
            generated_mouse_ids = self.progressive_mouse_ids()
            too_long = [
                mouse_id
                for mouse_id in generated_mouse_ids
                if len(mouse_id) > 100
            ]

            if too_long:
                self.add_error(
                    "mouse_id_prefix",
                    "Generated mouse IDs must be 100 characters or fewer.",
                )

            existing_mouse_ids = list(
                Mouse.objects
                .filter(pk__in=generated_mouse_ids)
                .order_by("mouse_id")
                .values_list("mouse_id", flat=True)
            )

            if existing_mouse_ids:
                shown_mouse_ids = ", ".join(existing_mouse_ids[:5])

                if len(existing_mouse_ids) > 5:
                    shown_mouse_ids = f"{shown_mouse_ids}, ..."

                self.add_error(
                    "mouse_id_prefix",
                    f"Generated mouse IDs already exist: {shown_mouse_ids}.",
                )
        else:
            for field_name in ("date_of_birth", "breeding_pair"):
                if not cleaned_data.get(field_name):
                    self.add_error(
                        field_name,
                        "Required when mouse IDs are generated automatically.",
                    )

        has_procedure_fields = any(
            cleaned_data.get(field_name)
            for field_name in (
                "procedure_date",
                "procedure_lab_member",
                "procedure_lab_book_page",
            )
        )
        has_procedure_request = (
            cleaned_data.get("procedure_type") is not None
            or has_procedure_fields
        )

        protocol = cleaned_data.get("protocol")

        if (
            protocol is not None
            and not protocol.allows_regulated_procedures
            and has_procedure_request
        ):
            self.add_error(
                "procedure_type",
                "This protocol does not allow regulated procedures.",
            )
        if has_procedure_fields and cleaned_data.get("procedure_type") is None:
            self.add_error(
                "procedure_type",
                "Select a procedure type before setting procedure fields.",
            )

        return cleaned_data

    @property
    def id_bound_fields(self):
        return [
            self[field_name]
            for field_name in self.ID_FIELDS
        ]

    @property
    def mouse_bound_field_sections(self):
        field_names = []

        for field_name in self.MOUSE_FIELDS:
            field_names.append(field_name)

            if field_name == "crossing_search":
                field_names.extend(self.genotype_fields)

        return visible_field_sections_for_names(
            self,
            field_names,
            self.genotype_fields,
        )

    @property
    def procedure_bound_fields(self):
        return [
            self[field_name]
            for field_name in self.PROCEDURE_FIELDS
        ]

    def uses_progressive_mouse_ids(self):
        if not hasattr(self, "cleaned_data"):
            return False

        return bool(
            self.cleaned_data.get("use_progressive_mouse_ids")
            or self.cleaned_data.get("mouse_id_prefix")
            or self.cleaned_data.get("mouse_id_start") is not None
        )

    def progressive_mouse_ids(self):
        if not hasattr(self, "cleaned_data"):
            return []

        mouse_count = self.cleaned_data.get("mouse_count")
        tattoo_start = self.cleaned_data.get("tattoo_start")

        if mouse_count is None or tattoo_start is None:
            return []

        mouse_id_start = self.cleaned_data.get("mouse_id_start")

        if mouse_id_start is None:
            mouse_id_start = tattoo_start

        prefix = self.cleaned_data.get("mouse_id_prefix") or ""

        return [
            f"{prefix}{mouse_id_start + offset}"
            for offset in range(mouse_count)
        ]

    def tattoos(self):
        if not hasattr(self, "cleaned_data"):
            return []

        mouse_count = self.cleaned_data.get("mouse_count")
        tattoo_start = self.cleaned_data.get("tattoo_start")

        if mouse_count is None or tattoo_start is None:
            return []

        return [
            str(tattoo_start + offset)
            for offset in range(mouse_count)
        ]


class MouseEditForm(MouseStructuredGeneticsMixin, forms.ModelForm):
    class Meta:
        model = Mouse
        fields = MOUSE_EDIT_FIELDS

        widgets = {
            **DATE_WIDGETS,
            "notes": forms.Textarea(attrs={"rows": 5}),
        }
        labels = {
            "tattoo": LOCAL_IDENTIFIER_LABEL,
        }
        help_texts = {
            "tattoo": LOCAL_IDENTIFIER_HELP_TEXT,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["protocol"].required = True
        self.fields["protocol"].empty_label = None
        self.fields["protocol"].queryset = Protocol.objects.order_by(
            "licence_reference",
            "protocol_number",
            "protocol_id",
        )
        self.configure_genetics_fields()

    def clean_protocol(self):
        protocol = self.cleaned_data.get("protocol")

        if protocol is None:
            raise forms.ValidationError("Select a protocol.")

        if (
            self.instance.pk
            and self.instance.protocol_id != protocol.pk
            and not protocol.allows_regulated_procedures
            and self.instance.procedures.exists()
        ):
            raise forms.ValidationError(
                "Remove existing procedures before moving this mouse to a "
                "protocol that does not allow regulated procedures."
            )

        return protocol


class MouseRenameForm(forms.Form):
    new_mouse_id = forms.CharField(
        max_length=100,
        label="New mouse ID",
    )
    confirm_new_mouse_id = forms.CharField(
        max_length=100,
        label="Confirm new mouse ID",
    )

    def __init__(self, *args, **kwargs):
        self.mouse = kwargs.pop("mouse")
        super().__init__(*args, **kwargs)

    def clean_new_mouse_id(self):
        new_mouse_id = self.cleaned_data["new_mouse_id"].strip()

        if new_mouse_id == self.mouse.pk:
            raise forms.ValidationError("Enter a different mouse ID.")

        if Mouse.objects.filter(pk=new_mouse_id).exists():
            raise forms.ValidationError(
                "A mouse with this ID already exists."
            )

        return new_mouse_id

    def clean_confirm_new_mouse_id(self):
        return self.cleaned_data["confirm_new_mouse_id"].strip()

    def clean(self):
        cleaned_data = super().clean()
        new_mouse_id = cleaned_data.get("new_mouse_id")
        confirm_new_mouse_id = cleaned_data.get("confirm_new_mouse_id")

        if (
            new_mouse_id
            and confirm_new_mouse_id
            and new_mouse_id != confirm_new_mouse_id
        ):
            self.add_error(
                "confirm_new_mouse_id",
                "The mouse IDs do not match.",
            )

        return cleaned_data


class MouseDeleteConfirmForm(forms.Form):
    confirm_mouse_id = forms.CharField(
        max_length=100,
        label="Type the mouse ID to confirm",
    )

    def __init__(self, *args, **kwargs):
        self.mouse = kwargs.pop("mouse")
        super().__init__(*args, **kwargs)

    def clean_confirm_mouse_id(self):
        confirm_mouse_id = self.cleaned_data["confirm_mouse_id"].strip()

        if confirm_mouse_id != self.mouse.pk:
            raise forms.ValidationError(
                "Type the current mouse ID exactly to confirm deletion."
            )

        return confirm_mouse_id


class BulkMouseDeleteConfirmForm(forms.Form):
    confirm_delete = forms.BooleanField(
        required=True,
        label="I understand these mice will be deleted.",
    )


class BaseProcedureForm(forms.ModelForm):
    class Meta:
        model = Procedure
        fields = [
            "procedure_type",
            "date",
            "lab_member",
            "lab_book_page",
        ]
        labels = {
            "procedure_type": "Procedure type",
            "date": "Date",
            "lab_book_page": "Lab-book page",
        }
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        self.mouse = kwargs.pop("mouse", None)
        super().__init__(*args, **kwargs)

        self.fields["procedure_type"].queryset = (
            ProcedureType.objects.all().order_by("name")
        )


class ProcedureCreateForm(BaseProcedureForm):
    class Meta(BaseProcedureForm.Meta):
        help_texts = {
            "procedure_type": (
                "This always adds a new procedure row for the mouse."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.can_create_procedure = (
            self.mouse is None
            or self.mouse.protocol.allows_regulated_procedures
        )

        if not self.can_create_procedure:
            for field in self.fields.values():
                field.disabled = True
                field.required = False

            self.fields["procedure_type"].help_text = (
                "Regulated procedure creation is disabled for this protocol."
            )

    def clean(self):
        cleaned_data = super().clean()

        if not self.can_create_procedure:
            raise forms.ValidationError(
                "This protocol does not allow regulated procedures."
            )

        return cleaned_data

class ProcedureEditForm(BaseProcedureForm):
    class Meta(BaseProcedureForm.Meta):
        help_texts = {
            "procedure_type": "Changing this edits only the selected procedure row.",
        }


class BulkMouseUpdateForm(forms.Form):
    MOUSE_UPDATE_FIELDS = [
        ("update_status", "status"),
        ("update_severity", "severity"),
        ("update_protocol", "protocol"),
        ("update_protocol_start_date", "protocol_start_date"),
        ("update_cull_date", "cull_date"),
        ("update_sex", "sex"),
        ("update_crossing", "crossing_search"),
        ("update_tattoo", "tattoo"),
        ("update_date_of_birth", "date_of_birth"),
        ("update_breeding_pair", "breeding_pair"),
    ]
    PROCEDURE_UPDATE_FIELDS = [
        ("update_procedure_date", "date", "procedure_date"),
        ("update_procedure_lab_member", "lab_member", "procedure_lab_member"),
        (
            "update_procedure_lab_book_page",
            "lab_book_page",
            "procedure_lab_book_page",
        ),
    ]

    update_status = forms.BooleanField(
        required=False,
        label="Update status",
    )
    status = forms.CharField(
        required=False,
        max_length=50,
        label="Status",
    )

    update_severity = forms.BooleanField(
        required=False,
        label="Update max severity",
    )
    severity = forms.ChoiceField(
        required=False,
        choices=[("", "---------"), *MAX_SEVERITY_CHOICES],
        label="Max severity",
    )

    update_protocol = forms.BooleanField(
        required=False,
        label="Update protocol",
    )
    protocol = forms.ModelChoiceField(
        required=False,
        queryset=Protocol.objects.all().order_by("protocol_number"),
        label="Protocol",
    )

    update_protocol_start_date = forms.BooleanField(
        required=False,
        label="Update protocol start date",
    )
    protocol_start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Protocol start date",
    )

    update_cull_date = forms.BooleanField(
        required=False,
        label="Update cull date",
    )
    cull_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Cull date",
    )

    update_sex = forms.BooleanField(
        required=False,
        label="Update sex",
    )
    sex = forms.ChoiceField(
        required=False,
        choices=[("", "---------"), *Mouse.SEX_CHOICES],
        label="Sex",
    )

    update_crossing = forms.BooleanField(
        required=False,
        label="Update crossing",
    )
    update_genotype = forms.BooleanField(
        required=False,
        label="Update genotype",
    )
    crossing_definition = forms.ModelChoiceField(
        required=False,
        queryset=Crossing.objects.none(),
        widget=forms.HiddenInput(attrs={"data-crossing-definition-input": "true"}),
        label="Crossing",
    )
    crossing_search = forms.CharField(
        required=False,
        label="Crossing",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "data-crossing-lookup-input": "true",
                "placeholder": "Type to search crossings",
            }
        ),
    )

    update_tattoo = forms.BooleanField(
        required=False,
        label="Update local identifier",
    )
    tattoo = forms.CharField(
        required=False,
        max_length=100,
        label=LOCAL_IDENTIFIER_LABEL,
        help_text=LOCAL_IDENTIFIER_HELP_TEXT,
    )

    update_date_of_birth = forms.BooleanField(
        required=False,
        label="Update date of birth",
    )
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Date of birth",
    )

    append_notes = forms.BooleanField(
        required=False,
        label="Append mouse notes",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Notes to append",
        help_text="These notes are appended to each selected mouse.",
    )

    procedure_type = forms.ModelChoiceField(
        required=False,
        queryset=ProcedureType.objects.all().order_by("name"),
        label="Procedure type to add",
        help_text="Selecting a procedure creates a new row for each selected mouse.",
    )

    update_procedure_date = forms.BooleanField(
        required=False,
        label="Set date",
    )
    procedure_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Date",
    )

    update_procedure_lab_member = forms.BooleanField(
        required=False,
        label="Set lab member",
    )
    procedure_lab_member = forms.CharField(
        required=False,
        max_length=100,
        label="Lab member",
    )

    update_procedure_lab_book_page = forms.BooleanField(
        required=False,
        label="Set lab-book page",
    )
    procedure_lab_book_page = forms.CharField(
        required=False,
        max_length=50,
        label="Lab-book page",
    )
    update_breeding_pair = forms.BooleanField(
        required=False,
        label="Update breeding pair",
    )
    breeding_pair = forms.CharField(
        required=False,
        max_length=100,
        label="Breeding pair",
    )

    def __init__(self, *args, **kwargs):
        self.selected_mice = list(kwargs.pop("selected_mice", []))
        super().__init__(*args, **kwargs)
        self.fields["crossing_definition"].queryset = active_crossings_queryset()
        self.genotype_fields = []
        self.current_crossing = self.shared_selected_crossing()
        self.selected_crossing = self.selected_crossing_for_fields()

        if self.selected_crossing is not None and not self.is_bound:
            self.fields["crossing_definition"].initial = self.selected_crossing
            self.fields["crossing_search"].initial = crossing_choice_label(
                self.selected_crossing
            )

        if self.selected_crossing is not None:
            genotype_form_fields = OrderedDict()

            for line in self.selected_crossing.lines.order_by(
                "name",
                "mouse_line_id",
            ):
                field_name = genotype_field_name(line)
                self.genotype_fields.append(field_name)
                genotype_form_fields[field_name] = forms.ChoiceField(
                    required=False,
                    choices=[("", "Leave unchanged"), *ZYGOSITY_CHOICES],
                    initial=self.shared_zygosity_initial(line),
                    label=line.name,
                    widget=forms.Select(
                        attrs={
                            "data-genotype-field": "true",
                            "data-line-id": line.pk,
                        }
                    ),
                )

            self.fields.update(genotype_form_fields)

    def shared_selected_crossing(self):
        crossing_ids = {
            mouse.crossing_definition_id
            for mouse in self.selected_mice
        }

        if len(crossing_ids) != 1:
            return None

        crossing_id = next(iter(crossing_ids))

        if crossing_id is None:
            return None

        try:
            return active_crossings_queryset().get(pk=crossing_id)
        except Crossing.DoesNotExist:
            return None

    def shared_zygosity_initial(self, line):
        mouse_ids = [
            mouse.pk
            for mouse in self.selected_mice
        ]

        if not mouse_ids:
            return ""

        calls = (
            MouseGenotype.objects
            .filter(mouse_id__in=mouse_ids, mouse_line=line)
            .values_list("mouse_id", "zygosity")
        )
        calls_by_mouse = {
            mouse_id: zygosity
            for mouse_id, zygosity in calls
        }
        zygosity_values = {
            calls_by_mouse.get(mouse_id)
            for mouse_id in mouse_ids
        }

        if len(zygosity_values) == 1:
            return next(iter(zygosity_values)) or ""

        return ""

    def selected_crossing_for_fields(self):
        if self.is_bound:
            raw_crossing_id = self.data.get(
                self.add_prefix("crossing_definition"),
                "",
            )

            if raw_crossing_id:
                try:
                    return active_crossings_queryset().get(pk=raw_crossing_id)
                except (Crossing.DoesNotExist, ValueError):
                    return None

            return None

        if self.current_crossing is not None:
            return self.current_crossing

        initial_crossing = self.initial.get("crossing_definition")

        if isinstance(initial_crossing, Crossing):
            return initial_crossing

        if initial_crossing:
            try:
                return active_crossings_queryset().get(pk=initial_crossing)
            except (Crossing.DoesNotExist, ValueError):
                return None

        return None

    def clean(self):
        cleaned_data = super().clean()
        has_mouse_update = any(
            cleaned_data.get(update_field)
            for update_field, _ in self.MOUSE_UPDATE_FIELDS
        )
        has_notes_update = cleaned_data.get("append_notes")
        has_procedure_field_update = any(
            cleaned_data.get(update_field)
            for update_field, _, _ in self.PROCEDURE_UPDATE_FIELDS
        )
        has_procedure_update = (
            cleaned_data.get("procedure_type") is not None
            or has_procedure_field_update
        )
        update_crossing = cleaned_data.get("update_crossing")
        update_genotype = cleaned_data.get("update_genotype")
        has_mouse_update = has_mouse_update or bool(update_genotype)

        if (
            cleaned_data.get("update_protocol")
            and cleaned_data.get("protocol") is None
        ):
            self.add_error("protocol", "Select a protocol.")

        if update_crossing or update_genotype:
            crossing = cleaned_data.get("crossing_definition")
            crossing_search = (cleaned_data.get("crossing_search") or "").strip()

            if crossing is None and crossing_search:
                matching_crossings = [
                    candidate
                    for candidate in active_crossings_queryset()
                    if crossing_choice_label(candidate).casefold()
                    == crossing_search.casefold()
                ]

                if len(matching_crossings) == 1:
                    crossing = matching_crossings[0]
                    cleaned_data["crossing_definition"] = crossing

            if crossing is None:
                self.add_error(
                    "crossing_search",
                    (
                        "Select an existing crossing, or select mice that "
                        "already share one crossing."
                    ),
                )
            elif (
                update_genotype
                and not update_crossing
                and self.current_crossing is not None
                and crossing.pk != self.current_crossing.pk
            ):
                self.add_error(
                    "crossing_search",
                    "Check Update crossing before using a different crossing.",
                )
            else:
                genotype_values = {}

                for line in crossing.lines.order_by("name", "mouse_line_id"):
                    field_name = genotype_field_name(line)
                    zygosity = cleaned_data.get(field_name)

                    if update_crossing:
                        genotype_values[line.pk] = zygosity or "unknown"
                    elif zygosity:
                        genotype_values[line.pk] = zygosity

                cleaned_data["genotype_values"] = genotype_values

        target_protocol = None
        if cleaned_data.get("update_protocol") and cleaned_data.get("protocol"):
            target_protocol = cleaned_data["protocol"]

        if (
            target_protocol is not None
            and not target_protocol.allows_regulated_procedures
        ):
            mice_with_procedures = [
                mouse.mouse_id
                for mouse in self.selected_mice
                if mouse.procedures.exists()
            ]

            if mice_with_procedures:
                raise forms.ValidationError(
                    "Remove existing procedures before moving these mice to a "
                    "protocol that does not allow regulated procedures: "
                    + ", ".join(mice_with_procedures[:5])
                )

        if has_procedure_update:
            disallowed_mouse_ids = []

            for mouse in self.selected_mice:
                protocol = mouse.protocol

                if target_protocol is not None:
                    protocol = target_protocol

                if not protocol.allows_regulated_procedures:
                    disallowed_mouse_ids.append(mouse.mouse_id)

            if disallowed_mouse_ids:
                raise forms.ValidationError(
                    "Regulated procedure creation is disabled for one or more "
                    "selected mice: "
                    + ", ".join(disallowed_mouse_ids[:5])
                )

        if has_notes_update and not cleaned_data.get("notes", "").strip():
            self.add_error(
                "notes",
                "Enter notes to append, or leave this update unchecked.",
            )

        if has_procedure_field_update and cleaned_data.get("procedure_type") is None:
            self.add_error(
                "procedure_type",
                "Select a procedure type before setting procedure fields.",
            )

        if not has_mouse_update and not has_notes_update and not has_procedure_update:
            raise forms.ValidationError(
                "Select at least one mouse update or procedure to add before saving."
            )

        return cleaned_data

    @property
    def mouse_update_bound_fields(self):
        fields = []

        for update_field, value_field in self.MOUSE_UPDATE_FIELDS:
            item = {
                "checkbox": self[update_field],
                "field": self[value_field],
            }

            if update_field == "update_crossing":
                item["secondary_checkbox"] = self["update_genotype"]
                item["genotype_fields"] = [
                    self[field_name]
                    for field_name in self.genotype_fields
                ]

            fields.append(item)

        return fields

    @property
    def procedure_update_bound_fields(self):
        return [
            {
                "checkbox": self[update_field],
                "field": self[value_field],
            }
            for update_field, _, value_field in self.PROCEDURE_UPDATE_FIELDS
        ]

    def selected_mouse_updates(self):
        return {
            value_field: self.cleaned_data[value_field]
            for update_field, value_field in self.MOUSE_UPDATE_FIELDS
            if (
                self.cleaned_data.get(update_field)
                and update_field != "update_crossing"
            )
        }

    def selected_structured_genetics_update(self):
        update_crossing = self.cleaned_data.get("update_crossing")
        update_genotype = self.cleaned_data.get("update_genotype")

        if not update_crossing and not update_genotype:
            return None

        return (
            self.cleaned_data["crossing_definition"],
            self.cleaned_data.get("genotype_values", {}),
            not update_crossing,
        )

    def notes_to_append(self):
        if self.cleaned_data.get("append_notes"):
            return self.cleaned_data.get("notes", "").strip()
        return ""

    def selected_procedure_updates(self):
        return {
            model_field: self.cleaned_data[value_field]
            for update_field, model_field, value_field in (
                self.PROCEDURE_UPDATE_FIELDS
            )
            if self.cleaned_data.get(update_field)
        }
