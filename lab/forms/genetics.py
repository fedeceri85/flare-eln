"""Crossing forms, structured genotype fields and synchronization."""

from collections import OrderedDict
from hashlib import sha256

from django import forms

from ..models import (
    Crossing,
    MAX_CROSSING_LINES,
    MouseGenotype,
    MouseLine,
    Species,
    genotype_category_choices,
    genotype_category_labels,
    resolve_crossing,
    crossing_display_name,
)


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


def individual_mouse_field_prefix(mouse):
    return f"individual_{sha256(str(mouse.pk).encode()).hexdigest()}"


def genotype_field_name(mouse_line):
    line_id = mouse_line.pk if hasattr(mouse_line, "pk") else mouse_line
    return f"genotype_line_{line_id}"


def truncate_compat_text(value, max_length=100):
    if len(value) <= max_length:
        return value

    return value[: max_length - 3].rstrip() + "..."


def genotype_summary(genotype_calls, uncertainty_values=None):
    uncertainty_values = uncertainty_values or {}
    labels = genotype_category_labels()
    return "; ".join(
        f"{line.name}: {labels.get(zygosity, zygosity)}"
        + (" (uncertain)" if uncertainty_values.get(line.pk) else "")
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
    uncertainty_values=None,
):
    line_ids = set(crossing.lines.values_list("pk", flat=True))

    mouse.crossing_definition = crossing
    mouse.crossing = truncate_compat_text(crossing.display_name)
    mouse.crossing_type = crossing.inferred_crossing_type

    lines = list(crossing.ordered_lines())
    existing_calls = {}

    if preserve_missing:
        existing_calls = {
            genotype.mouse_line_id: genotype.zygosity
            for genotype in mouse.genotype_calls.filter(mouse_line_id__in=line_ids)
        }

    uncertainty_values = uncertainty_values or {}
    existing_uncertainty = dict(mouse.genotype_calls.values_list(
        "mouse_line_id", "is_uncertain",
    ))
    saved_uncertainty = {}
    summary_values = []

    for line in lines:
        zygosity = genotype_values.get(line.pk)

        if zygosity is None and preserve_missing:
            zygosity = existing_calls.get(line.pk)

        zygosity = zygosity or "unknown"
        is_uncertain = uncertainty_values.get(
            line.pk, existing_uncertainty.get(line.pk, False),
        )
        saved_uncertainty[line.pk] = is_uncertain
        MouseGenotype.objects.update_or_create(
            mouse=mouse,
            mouse_line=line,
            defaults={"zygosity": zygosity, "is_uncertain": is_uncertain},
        )
        summary_values.append((line, zygosity))

    MouseGenotype.objects.filter(mouse=mouse).exclude(
        mouse_line_id__in=line_ids,
    ).delete()

    mouse.genotype = truncate_compat_text(genotype_summary(summary_values, saved_uncertainty))
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
    aliases = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Alternative names, separated by commas. Applied to each new line when adding multiple lines.",
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

        if any(len(name) > MouseLine._meta.get_field("name").max_length for name in names):
            raise forms.ValidationError("Line names must contain at most 200 characters.")
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
                    aliases=self.cleaned_data["aliases"],
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

        cleaned_data["lines"] = [lines_by_id[line_id] for line_id in line_ids]
        if len(crossing_display_name(cleaned_data["lines"])) > Crossing._meta.get_field("display_name").max_length:
            self.add_error("selected_lines", "The resulting crossing name is too long (maximum 500 characters).")
        return cleaned_data

    def save(self):
        return resolve_crossing(
            self.cleaned_data["species"],
            self.cleaned_data["lines"],
        )


class MouseStructuredGeneticsMixin:
    def clean_marking_type(self):
        return self.cleaned_data.get("marking_type") or "other"

    def configure_genetics_fields(self):
        if "marking_type" in self.fields:
            self.fields["marking_type"].required = False
            # Preserve legacy types on edits; new instances use the model default.
            self.fields["marking_type"].disabled = True
            self.fields["marking_type"].widget = forms.HiddenInput()
            self.fields["marking_type"].initial = self.instance.marking_type or "other"
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

        if self.selected_crossing is None or getattr(self, "individual_genotypes", False):
            return

        existing_calls = {}

        if self.instance.pk:
            existing_calls = {
                genotype.mouse_line_id: genotype.zygosity
                for genotype in self.instance.genotype_calls.all()
            }

        genotype_form_fields = OrderedDict()
        choices = genotype_category_choices(existing_calls.values())

        for line in self.selected_crossing.ordered_lines():
            field_name = genotype_field_name(line)
            self.genotype_fields.append(field_name)
            genotype_form_fields[field_name] = forms.ChoiceField(
                required=False,
                choices=choices,
                initial=existing_calls.get(line.pk, "unknown"),
                label=line.name,
                widget=forms.Select(
                    attrs={
                        "data-genotype-field": "true",
                        "data-line-id": line.pk,
                    }
                ),
            )

        existing_uncertainty = dict(self.instance.genotype_calls.values_list(
            "mouse_line_id", "is_uncertain",
        )) if self.instance.pk else {}
        for line in self.selected_crossing.ordered_lines():
            genotype_form_fields[f"{genotype_field_name(line)}_uncertain"] = forms.BooleanField(
                required=False, initial=existing_uncertainty.get(line.pk, False),
                label="Uncertain", widget=forms.CheckboxInput(attrs={
                    "data-genotype-uncertainty": "true", "data-line-id": line.pk,
                    "aria-label": f"{line.name}: uncertain result",
                }),
            )
        self.insert_genotype_fields(genotype_form_fields)
        for field_name in self.genotype_fields:
            self[field_name].uncertainty_field = self[f"{field_name}_uncertain"]

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
            if field.field.widget.attrs.get("data-genotype-uncertainty"):
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

        for line in crossing.ordered_lines():
            field_name = genotype_field_name(line)
            zygosity = cleaned_data.get(field_name)

            if not zygosity:
                zygosity = existing_calls.get(line.pk, "unknown")

            genotype_values[line.pk] = zygosity

        cleaned_data["genotype_values"] = genotype_values
        cleaned_data["uncertainty_values"] = {
            line.pk: cleaned_data.get(f"{genotype_field_name(line)}_uncertain", False)
            for line in crossing.ordered_lines()
        }
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
            uncertainty_values=self.cleaned_data.get("uncertainty_values", {}),
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


class LineRenameForm(forms.Form):
    name = forms.CharField(max_length=200, label="Line name")
    aliases = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Alternative names, separated by commas.",
    )


class CrossingOrderForm(forms.Form):
    selected_lines = forms.CharField(widget=forms.HiddenInput)

    def clean_selected_lines(self):
        try:
            return [int(value) for value in self.cleaned_data["selected_lines"].split(",")]
        except (ValueError, TypeError):
            raise forms.ValidationError("Select a valid order for the existing lines.")
