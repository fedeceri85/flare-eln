"""Shared and individual changes for existing mouse selections."""

from collections import OrderedDict

from django import forms

from ..models import (
    Crossing,
    Mouse,
    MouseGenotype,
    Protocol,
    MAX_SEVERITY_CHOICES,
    genotype_category_choices,
)
from .genetics import (
    active_crossings_queryset,
    crossing_choice_label,
    genotype_field_name,
    individual_mouse_field_prefix,
)


class BulkMouseUpdateForm(forms.Form):
    MOUSE_UPDATE_GROUPS = (
        ("Origin", ("date_of_birth", "breeding_pair")),
        ("Animal status", ("status", "severity", "cull_date")),
        ("Protocol", ("protocol", "protocol_start_date")),
    )

    MOUSE_UPDATE_FIELDS = [
        ("update_status", "status"),
        ("update_severity", "severity"),
        ("update_protocol", "protocol"),
        ("update_protocol_start_date", "protocol_start_date"),
        ("update_cull_date", "cull_date"),
        ("update_crossing", "crossing_search"),
        ("update_date_of_birth", "date_of_birth"),
        ("update_breeding_pair", "breeding_pair"),
    ]


    confirm_mouse_ids = forms.BooleanField(required=False, label="Confirm Animal ID changes")

    update_status = forms.BooleanField(
        required=False,
        label="Update status",
    )
    status = forms.ChoiceField(
        required=False,
        choices=Mouse.STATUS_CHOICES,
        initial="Alive",
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

    update_date_of_birth = forms.BooleanField(
        required=False,
        label="Update date of birth",
    )
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Date of birth",
    )

    notes_action = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Leave unchanged"),
            ("append", "Append"),
            ("replace", "Replace"),
        ],
        label="Animal notes action",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Animal notes",
        help_text=(
            "Append adds this text to each selected animal. Replace overwrites "
            "their existing notes; leave the text blank to clear them."
        ),
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
        statuses = {mouse.status for mouse in self.selected_mice}
        if len(statuses) == 1 and next(iter(statuses)) in dict(Mouse.STATUS_CHOICES):
            self.fields["status"].initial = next(iter(statuses))
        self.single_mouse_mode = len(self.selected_mice) == 1
        if self.single_mouse_mode:
            mouse = self.selected_mice[0]
            if self.is_bound:
                self.data = self.data.copy()
            for update_name, name in self.MOUSE_UPDATE_FIELDS:
                if name != "crossing_search":
                    value = getattr(mouse, name)
                    if name == "status" and value not in dict(Mouse.STATUS_CHOICES):
                        value = self.fields["status"].initial
                    self.fields[name].initial = value
                    if (self.is_bound and self.add_prefix(name) not in self.data
                            and not self.fields[update_name].clean(self.data.get(self.add_prefix(update_name)))):
                        if name == "protocol":
                            value = mouse.protocol_id
                        elif hasattr(value, "isoformat"):
                            value = value.isoformat()
                        self.data[self.add_prefix(name)] = value if value is not None else ""
            if mouse.crossing_definition_id is None:
                self.fields["crossing_search"].initial = mouse.crossing
            self.fields["notes"].initial = mouse.notes
            self.fields["update_genotype"].widget = forms.HiddenInput()
            for name in ("date_of_birth", "protocol_start_date", "cull_date"):
                self.fields[name].widget.format = "%Y-%m-%d"
        if self.is_bound and not self.fields["update_status"].clean(self.data.get(self.add_prefix("update_status"))):
            self.fields["status"].disabled = True
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
            existing_codes = MouseGenotype.objects.filter(
                mouse__in=self.selected_mice,
            ).values_list("zygosity", flat=True)
            choices = genotype_category_choices(existing_codes)

            for line in self.selected_crossing.ordered_lines():
                field_name = genotype_field_name(line)
                self.genotype_fields.append(field_name)
                genotype_form_fields[field_name] = forms.ChoiceField(
                    required=False,
                    choices=[("", "Leave unchanged"), *choices],
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

        self.configure_individual_rows()

    def configure_individual_rows(self):
        self.individual_rows = []
        self.identity_rows = []
        self.individual_row_details = []
        calls = {}
        for call in MouseGenotype.objects.filter(mouse__in=self.selected_mice):
            calls.setdefault(call.mouse_id, {})[call.mouse_line_id] = call
        choices = genotype_category_choices(
            call.zygosity for mouse_calls in calls.values() for call in mouse_calls.values()
        )
        lines_by_crossing = {}
        crossings = {crossing.pk: crossing for crossing in Crossing.objects.filter(
            pk__in={mouse.crossing_definition_id for mouse in self.selected_mice if mouse.crossing_definition_id},
        )}
        change_crossing = self.is_bound and self.data.get(self.add_prefix("update_crossing"))
        for mouse in self.selected_mice:
            stem = individual_mouse_field_prefix(mouse)
            self.fields[f"{stem}_mouse_id"] = forms.CharField(
                required=False, max_length=100, initial=mouse.pk, label="Animal ID",
                widget=forms.TextInput(attrs={"aria-label": f"{mouse.pk}: Animal ID"}),
            )
            self.identity_rows.append({"mouse_id": mouse.pk, "field": self[f"{stem}_mouse_id"]})
            crossing = self.selected_crossing if change_crossing and self.selected_crossing else crossings.get(mouse.crossing_definition_id)
            if crossing and crossing.pk not in lines_by_crossing:
                lines_by_crossing[crossing.pk] = crossing.ordered_lines()
            lines = lines_by_crossing.get(crossing.pk, []) if crossing else []
            self.fields[f"{stem}_present"] = forms.BooleanField(
                required=False, initial=True, widget=forms.HiddenInput,
            )
            self.fields[f"{stem}_local_identifier"] = forms.CharField(
                required=False, max_length=100, initial=mouse.tattoo,
                label=f"{mouse.pk}: local identifier",
                widget=forms.TextInput(attrs={"aria-label": f"{mouse.pk}: local identifier"}),
            )
            self.fields[f"{stem}_sex"] = forms.ChoiceField(
                required=False, choices=[("", "Unknown"), *Mouse.SEX_CHOICES], initial=mouse.sex,
                label=f"{mouse.pk}: sex", widget=forms.Select(attrs={"aria-label": f"{mouse.pk}: sex"}),
            )
            genotype_fields = []
            row_calls = calls.get(mouse.pk, {})
            for line in lines:
                name = f"{stem}_{genotype_field_name(line)}"
                call = row_calls.get(line.pk)
                self.fields[name] = forms.ChoiceField(
                    required=False, choices=choices, initial=call.zygosity if call else "unknown",
                    label=line.name, widget=forms.Select(attrs={
                        "aria-label": f"{mouse.pk}: {line.name} genotype",
                        "data-individual-genotype-field": "true", "data-line-id": line.pk,
                    }),
                )
                self.fields[f"{name}_uncertain"] = forms.BooleanField(
                    required=False, initial=call.is_uncertain if call else False, label="Uncertain",
                    widget=forms.CheckboxInput(attrs={"aria-label": f"{mouse.pk}: {line.name} uncertain result"}),
                )
                self[name].uncertainty_field = self[f"{name}_uncertain"]
                genotype_fields.append(self[name])
            self.individual_rows.append({
                "mouse_id": mouse.pk, "local_identifier": self[f"{stem}_local_identifier"],
                "crossing": crossing.display_name if crossing else mouse.crossing,
                "sex": self[f"{stem}_sex"], "genotype_fields": genotype_fields,
                "legacy_genotype": mouse.genotype if not crossing else "",
            })
            self.individual_row_details.append((mouse, stem, crossing, lines, row_calls))

    def clean_individual_updates(self, cleaned_data):
        updates = {}
        for mouse, stem, crossing, lines, calls in self.individual_row_details:
            fields = {}
            sex_name = f"{stem}_sex"
            if self.add_prefix(sex_name) in self.data and sex_name in cleaned_data:
                if cleaned_data[sex_name] != mouse.sex:
                    fields["sex"] = cleaned_data[sex_name]
            identifier_name = f"{stem}_local_identifier"
            if self.add_prefix(identifier_name) in self.data and identifier_name in cleaned_data:
                if cleaned_data[identifier_name] != mouse.tattoo:
                    fields["tattoo"] = cleaned_data[identifier_name]
            genotype_values = {}
            uncertainty_values = {}
            for line in lines:
                name = f"{stem}_{genotype_field_name(line)}"
                call = calls.get(line.pk)
                if self.add_prefix(name) in self.data and name in cleaned_data:
                    value = cleaned_data[name] or "unknown"
                    if (
                        cleaned_data.get("update_genotype") or cleaned_data.get("update_crossing")
                        or value != (call.zygosity if call else "unknown")
                    ):
                        genotype_values[line.pk] = value
                uncertainty_name = f"{name}_uncertain"
                if cleaned_data.get(f"{stem}_present") or self.add_prefix(uncertainty_name) in self.data:
                    value = cleaned_data.get(uncertainty_name, False)
                    if value != (call.is_uncertain if call else False):
                        uncertainty_values[line.pk] = value
            if fields or genotype_values or uncertainty_values:
                updates[mouse.pk] = {
                    "fields": fields, "crossing": crossing,
                    "genotype_values": genotype_values, "uncertainty_values": uncertainty_values,
                }
        return updates

    def clean_mouse_ids(self, cleaned_data):
        renames = {}
        final_ids = {}
        for row in self.identity_rows:
            field = row["field"]
            old_id = row["mouse_id"]
            if self.add_prefix(field.name) not in self.data or field.name not in cleaned_data:
                final_ids[old_id] = old_id
                continue
            value = (cleaned_data[field.name] or "").strip()
            if not value:
                self.add_error(field.name, "Enter an Animal ID.")
            final_ids[old_id] = value
            if value and value != old_id:
                renames[old_id] = value
        selected_ids = {mouse.pk for mouse in self.selected_mice}
        occupied = set(Mouse.objects.exclude(pk__in=selected_ids).filter(pk__in=renames.values()).values_list("pk", flat=True))
        for row in self.identity_rows:
            value = final_ids[row["mouse_id"]]
            if value and (value in occupied or list(final_ids.values()).count(value) > 1):
                self.add_error(row["field"].name, "An animal with this ID already exists or is named twice in this selection.")
        if renames and not cleaned_data.get("confirm_mouse_ids"):
            self.add_error("confirm_mouse_ids", "Confirm the Animal ID changes before saving.")
        return renames

    def validate_local_identifiers(self, cleaned_data):
        # Match the existing database rule against the final state of the batch.
        # Assigned mice and blank identifiers are excluded by that rule.
        candidates = []
        for mouse, stem, _, _, _ in self.individual_row_details:
            if mouse.pk in cleaned_data.get("mouse_id_renames", {}) or mouse.id_status not in {Mouse.ID_TEMPORARY, Mouse.ID_UNKNOWN}:
                continue
            updates = cleaned_data["individual_updates"].get(mouse.pk, {}).get("fields", {})
            identifier = updates.get("tattoo", mouse.tattoo)
            dob = cleaned_data.get("date_of_birth") if cleaned_data.get("update_date_of_birth") else mouse.date_of_birth
            pair = cleaned_data.get("breeding_pair", "") if cleaned_data.get("update_breeding_pair") else mouse.breeding_pair
            crossing = cleaned_data.get("crossing_definition") if cleaned_data.get("update_crossing") else None
            crossing_id = crossing.pk if crossing else mouse.crossing_definition_id
            if identifier and dob is not None and crossing_id is not None:
                candidates.append((stem, (dob, pair, crossing_id, identifier, cleaned_data.get("marking_type") if cleaned_data.get("update_marking_type") else mouse.marking_type)))
        if not candidates:
            return
        outside_keys = set(Mouse.objects.exclude(pk__in=[mouse.pk for mouse in self.selected_mice]).filter(
            id_status__in=[Mouse.ID_TEMPORARY, Mouse.ID_UNKNOWN],
            tattoo__in={key[3] for _, key in candidates},
        ).values_list("date_of_birth", "breeding_pair", "crossing_definition_id", "tattoo", "marking_type"))
        seen = set()
        for stem, key in candidates:
            if key in seen or key in outside_keys:
                self.add_error(f"{stem}_local_identifier", "This local identifier is already used by another animal with the same birth date, breeding pair, crossing and Local ID type.")
            seen.add(key)

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
        cleaned_data["mouse_id_renames"] = self.clean_mouse_ids(cleaned_data)
        has_mouse_update = bool(cleaned_data["mouse_id_renames"]) or any(
            cleaned_data.get(update_field)
            for update_field, _ in self.MOUSE_UPDATE_FIELDS
        )
        has_notes_update = cleaned_data.get("notes_action") in {"append", "replace"}
        cleaned_data["individual_updates"] = self.clean_individual_updates(cleaned_data)
        has_mouse_update = has_mouse_update or bool(cleaned_data["individual_updates"])
        update_crossing = cleaned_data.get("update_crossing")
        update_genotype = cleaned_data.get("update_genotype")
        has_mouse_update = has_mouse_update or bool(update_genotype)

        if cleaned_data.get("update_status") and not cleaned_data.get("status") and "status" not in self.errors:
            self.add_error("status", "Select Alive or Culled.")

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
                        "Select an existing crossing, or select animals that "
                        "already share one crossing."
                    ),
                )
            elif (
                update_genotype
                and not update_crossing
                and any(mouse.crossing_definition_id != crossing.pk for mouse in self.selected_mice)
            ):
                self.add_error(
                    "crossing_search",
                    "Check Update crossing before using a different crossing.",
                )
            else:
                genotype_values = {}

                for line in crossing.ordered_lines():
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
                    "Remove existing procedures before moving these animals to a "
                    "protocol that does not allow regulated procedures: "
                    + ", ".join(mice_with_procedures[:5])
                )

        if cleaned_data.get("notes_action") == "append" and not cleaned_data.get("notes", "").strip():
            self.add_error(
                "notes",
                "Enter notes to append, or choose Leave unchanged.",
            )

        if not has_mouse_update and not has_notes_update:
            raise forms.ValidationError("Select at least one animal change before saving.")

        self.validate_local_identifiers(cleaned_data)
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
    def mouse_update_groups(self):
        fields = {item["field"].name: item for item in self.mouse_update_bound_fields}
        return [
            {"title": title, "fields": [fields[name] for name in names]}
            for title, names in self.MOUSE_UPDATE_GROUPS
        ]

    @property
    def shared_genetics_update(self):
        return next(item for item in self.mouse_update_bound_fields if item.get("secondary_checkbox"))

    def selected_mouse_updates(self):
        updates = {
            value_field: self.cleaned_data[value_field]
            for update_field, value_field in self.MOUSE_UPDATE_FIELDS
            if (
                self.cleaned_data.get(update_field)
                and update_field != "update_crossing"
            )
        }

        return updates

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
