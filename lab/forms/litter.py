"""Litter creation and pending procedure validation."""

from django import forms

from ..utils import litter_id_prefix, parse_mouse_numbers
from ..models import (
    Crossing,
    Mouse,
    Protocol,
    MAX_SEVERITY_CHOICES,
    genotype_category_choices,
)
from .genetics import (
    MouseStructuredGeneticsMixin,
    genotype_field_name,
    visible_field_sections_for_names,
)
from .procedures import BulkProcedureCreateForm


class LitterCreateForm(MouseStructuredGeneticsMixin, forms.Form):
    ID_FIELDS = ["no_id_available", "mouse_id_prefix", "mouse_numbers"]
    MOUSE_FIELDS = [
        "date_of_birth",
        "breeding_pair",
        "crossing_search",
        "marking_type",
        "status",
        "protocol",
        "protocol_start_date",
        "cull_date",
        "severity",
        "notes",
    ]
    dynamic_mouse_count = True

    marking_type = forms.ChoiceField(
        required=False, choices=Mouse.MARKING_CHOICES, initial="other", label="Local ID type",
        help_text="Applies to every animal in this litter. Other is the default; local identifiers can be left blank.",
    )
    no_id_available = forms.BooleanField(
        required=False, label="No ID available — generate IDs",
        help_text="Fill the prefix from date of birth, breeding pair and crossing. Uncheck to use your own prefix or leave it blank.",
    )
    mouse_id_prefix = forms.CharField(
        required=False, max_length=100, label="Prefix",
        help_text="Optional. Animal IDs are this prefix followed by each number. For one arbitrary ID, enter the full ID here and leave Numbers blank.",
    )
    mouse_numbers = forms.CharField(
        required=False, max_length=5000, label="Numbers",
        help_text="Enter numbers, ranges or both: 1,3,5-7 creates five animals. Leading zeros are preserved.",
        widget=forms.TextInput(attrs={"placeholder": "1,3,5-7"}),
    )
    breeding_pair = forms.CharField(
        required=False, max_length=100, label="Breeding pair",
        help_text="Required when No ID available is checked.",
    )

    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Date of birth",
        help_text="Required when No ID available is checked.",
    )
    crossing_definition = forms.ModelChoiceField(
        required=False,
        queryset=Crossing.objects.none(),
        label="Crossing",
    )
    status = forms.ChoiceField(
        choices=Mouse.STATUS_CHOICES,
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

    procedure_drafts = forms.JSONField(required=False, initial=list, widget=forms.HiddenInput)

    def clean_procedure_drafts(self):
        drafts = self.cleaned_data.get("procedure_drafts")
        if drafts is None:
            return []
        if not isinstance(drafts, list) or len(drafts) > 100:
            raise forms.ValidationError("Add up to 100 procedures as a list.")
        validated = []
        for index, draft in enumerate(drafts, 1):
            if (not isinstance(draft, dict)
                    or any(not isinstance(draft.get(name, ""), str)
                           for name in ("date", "lab_member", "lab_book_page"))
                    or not isinstance(draft.get("procedure_type", ""), (str, int))
                    or isinstance(draft.get("procedure_type"), bool)):
                raise forms.ValidationError(f"Procedure {index}: invalid procedure details.")
            procedure_form = BulkProcedureCreateForm({
                f"new_procedure-{name}": draft.get(name, "")
                for name in ("procedure_type", "date", "lab_member", "lab_book_page")
            })
            if not procedure_form.is_valid():
                errors = "; ".join(
                    f"{procedure_form.fields[name].label or name}: {', '.join(messages)}"
                    for name, messages in procedure_form.errors.items()
                )
                raise forms.ValidationError(f"Procedure {index}: {errors}")
            validated.append(procedure_form.cleaned_data)
        return validated

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
        self.crossing_lines = self.selected_crossing.ordered_lines() if self.selected_crossing else []
        if self.is_bound:
            self.data = self.data.copy()
            # Older callers may omit status; use the same default as a new Mouse.
            self.data.setdefault(self.add_prefix("status"), Mouse._meta.get_field("status").get_default())
            self.data[self.add_prefix("mouse_id_prefix")] = self.data.get(self.add_prefix("mouse_id_prefix"), "").strip()
        self.numbers_error = None
        self.numbers = []
        if self.is_bound:
            try:
                raw_numbers = self.data.get(self.add_prefix("mouse_numbers"), "")
                if not raw_numbers.strip() and self.data.get(self.add_prefix("mouse_id_prefix")) and not self.data.get(self.add_prefix("no_id_available")):
                    self.numbers = [""]
                else:
                    self.numbers = parse_mouse_numbers(raw_numbers)
            except ValueError as exc:
                self.numbers_error = str(exc)
        if self.is_bound and self.data.get(self.add_prefix("no_id_available")):
            try:
                dob = self.fields["date_of_birth"].clean(self.data.get(self.add_prefix("date_of_birth")))
                pair = self.data.get(self.add_prefix("breeding_pair"), "").strip()
                prefix = litter_id_prefix(dob, pair, self.selected_crossing.display_name) if dob and pair and self.selected_crossing else ""
            except forms.ValidationError:
                prefix = ""
            self.data = self.data.copy()
            self.data[self.add_prefix("mouse_id_prefix")] = prefix
            self.fields["mouse_id_prefix"].widget.attrs["readonly"] = True
        self.single_mouse_mode = len(self.numbers) <= 1
        self.mouse_rows = []
        choices = genotype_category_choices()
        for number in self.numbers:
            stem = f"pup_{number}"
            for suffix, field in {
                "marking": forms.CharField(required=False, max_length=100, label="Local identifier"),
                "sex": forms.ChoiceField(required=False, choices=[("", "Unknown"), *Mouse.SEX_CHOICES], label="Sex"),
            }.items():
                field.widget.attrs["aria-label"] = f"Animal {number}: {field.label}"
                self.fields[f"{stem}_{suffix}"] = field
            genotype_fields = []
            for line in self.crossing_lines:
                name = f"{stem}_{genotype_field_name(line)}"
                shared_name = genotype_field_name(line)
                if (self.is_bound
                        and (self.add_prefix(name) not in self.data or "apply_shared_genotype" in self.data)):
                    # Seed new rows, preserving every explicitly posted individual result.
                    self.data[self.add_prefix(name)] = self.data.get(self.add_prefix(shared_name), "unknown")
                    uncertainty_name = self.add_prefix(f"{name}_uncertain")
                    if uncertainty_name not in self.data or "apply_shared_genotype" in self.data:
                        self.data[uncertainty_name] = self.data.get(
                            self.add_prefix(f"{shared_name}_uncertain"), "")
                self.fields[name] = forms.ChoiceField(required=False, choices=choices, initial="unknown", label=line.name,
                    widget=forms.Select(attrs={"data-individual-genotype-field": "true", "data-line-id": line.pk}))
                self.fields[name].widget.attrs["aria-label"] = f"Animal {number}: {line.name} genotype"
                self.fields[f"{name}_uncertain"] = forms.BooleanField(
                    required=False, label="Uncertain", widget=forms.CheckboxInput(attrs={
                        "aria-label": f"Animal {number}: {line.name} uncertain result",
                        "data-individual-genotype-uncertainty": "true", "data-line-id": line.pk,
                    }),
                )
                self[name].uncertainty_field = self[f"{name}_uncertain"]
                genotype_fields.append(self[name])
            self.mouse_rows.append({
                "number": number,
                "mouse_id": f'{self.data.get(self.add_prefix("mouse_id_prefix"), "")}{number}',
                "local_identifier": self[f"{stem}_marking"],
                "sex": self[f"{stem}_sex"], "genotype_fields": genotype_fields,
            })

    def clean(self):
        cleaned_data = super().clean()
        if self.numbers_error:
            self.add_error("mouse_numbers", self.numbers_error)
        if cleaned_data.get("no_id_available"):
            for field_name in ("date_of_birth", "breeding_pair"):
                if not cleaned_data.get(field_name):
                    self.add_error(field_name, "Required when No ID available is checked.")
        prefix = cleaned_data.get("mouse_id_prefix", "")
        mouse_ids = [f"{prefix}{number}" for number in self.numbers]
        if any(len(mouse_id) > 100 for mouse_id in mouse_ids):
            self.add_error("mouse_id_prefix", "Animal IDs must be 100 characters or fewer.")
        existing_ids = list(Mouse.objects.filter(pk__in=mouse_ids).values_list("pk", flat=True)[:5])
        if existing_ids:
            self.add_error("mouse_numbers", f"Animal IDs already exist: {', '.join(existing_ids)}.")
        markings = set()
        for number in self.numbers:
            name = f"pup_{number}_marking"
            marking = cleaned_data.get(name, "")
            if marking and cleaned_data.get("no_id_available"):
                if marking in markings:
                    self.add_error(name, "This local identifier is already used by another animal in this litter.")
                markings.add(marking)

        protocol = cleaned_data.get("protocol")
        if (protocol is not None and not protocol.allows_regulated_procedures
                and cleaned_data.get("procedure_drafts")):
            self.add_error("procedure_drafts", "This protocol does not allow regulated procedures.")

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
    def shared_mouse_field_groups(self):
        return [
            {"title": title, "fields": [self[name] for name in names]}
            for title, names in (
                ("Origin", ("date_of_birth", "breeding_pair", "marking_type")),
                ("Animal status", ("status", "severity", "cull_date")),
                ("Protocol", ("protocol", "protocol_start_date")),
            )
        ]

    def mouse_creation_rows(self):
        prefix = self.cleaned_data.get("mouse_id_prefix", "")
        for number in self.numbers:
            stem = f"pup_{number}"
            yield {
                "mouse_id": f"{prefix}{number}",
                "tattoo": self.cleaned_data.get(f"{stem}_marking", ""),
                "marking_type": self.cleaned_data.get("marking_type", ""),
                "sex": self.cleaned_data.get(f"{stem}_sex", ""),
                "genotype_values": {
                    line.pk: self.cleaned_data.get(f"{stem}_{genotype_field_name(line)}") or "unknown"
                    for line in self.crossing_lines
                },
                "uncertainty_values": {
                    line.pk: self.cleaned_data.get(f"{stem}_{genotype_field_name(line)}_uncertain", False)
                    for line in self.crossing_lines
                },
            }
