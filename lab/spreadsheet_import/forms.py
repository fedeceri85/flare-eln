"""Wizard forms translate user input into portable definitions or draft state."""
from copy import copy, deepcopy

from django import forms
from django.urls import reverse, reverse_lazy

from ..forms.recordings import RecordingForm
from ..models import Crossing, GenotypeCategory, MAX_SEVERITY_CHOICES, Mouse, MouseLine, Protocol, Species
from .engine import animal_display_details, animal_groups, build_review, digest, group_details, initial_resolution
from .mapping import crossing_candidates, crossing_suggestion, genotype_suggestion, genotype_term_key, mapped_value, normalize, parse_date, resolve_genotype_term, targets, typed_value
from .source import binding
from .procedures import procedure_definitions


def column_choices(table):
    return [("", "No source column"), *((str(c["index"]), c["label"]) for c in table["columns"])]


def column_initial(table, selected):
    return next((str(c["index"]) for c in table["columns"] if binding(c) == selected), "")


def column_definition(table, value):
    return binding(table["columns"][int(value)]) if value != "" else None


def crossing_select():
    # Keep a normal select when JavaScript is unavailable. The autocomplete
    # submits its selected ID through this same validated ModelChoiceField.
    return forms.Select(attrs={"data-import-crossing-select": reverse_lazy("lab:crossing_lookup")})


def shared_value_field(field, label):
    """A shared value is optional even when each resulting row requires a value."""
    if isinstance(field, forms.BooleanField):
        field = forms.TypedChoiceField(
            choices=[("", "No shared value"), ("true", "Yes"), ("false", "No")],
            coerce=lambda value: value == "true", empty_value="",
        )
    else:
        field = deepcopy(field)
    field.required = field.widget.is_required = False
    field.label = f"{label}: shared value"
    field.help_text = "Use this value for every selected row instead of a source column."
    if isinstance(field, forms.ChoiceField) and not any(value == "" for value, _ in field.choices):
        field.choices = [("", "No shared value"), *field.choices]
    if isinstance(field, forms.DateField):
        field.widget = forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})
    if isinstance(field.widget, forms.Textarea):
        field.widget.attrs["rows"] = 2
    return field


class UploadForm(forms.Form):
    spreadsheet = forms.FileField(label="Excel or CSV file", widget=forms.FileInput(attrs={"accept": ".xlsx,.csv"}))


class SourceForm(forms.Form):
    sheet = forms.ChoiceField(label="Worksheet")
    header_row = forms.IntegerField(min_value=1, label="Header row", initial=1)

    def __init__(self, draft, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sheet"].choices = [(s["name"], s["name"]) for s in draft.source]
        self.initial.update(sheet=draft.state["sheet"], header_row=draft.state["header_row"])


class SetupForm(forms.Form):
    date_order = forms.ChoiceField(label="Text date format", choices=[("DMY", "Day / month / year"), ("MDY", "Month / day / year"), ("YMD", "Year / month / day")])
    grouping = forms.ChoiceField(label="Create recordings", choices=[("rows", "One recording per selected row"), ("date", "Combine selected rows by date + crossing")])
    identity_columns = forms.MultipleChoiceField(required=False, label="Additional columns distinguishing local identifiers", help_text="Use columns such as birth date or breeding pair to distinguish reused local markings. Repeated Animal IDs always refer to the same animal.")
    protocol_id = forms.ModelChoiceField(queryset=Protocol.objects.all(), required=False, label="Shared protocol for new animals / proposed updates")
    protocol_start_date = forms.DateField(required=False, label="Shared protocol start date", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    species_id = forms.ModelChoiceField(queryset=Species.objects.filter(active=True), required=False, label="Species", empty_label="Any species")

    def __init__(self, draft, table, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.draft, self.table = draft, table
        definition = draft.configuration
        self.initial.update({k: definition[k] for k in ("date_order", "grouping")})
        self.fields["identity_columns"].choices = column_choices(table)[1:]
        self.initial["identity_columns"] = [column_initial(table, b) for b in definition.get("identity_columns", [])]
        for name in ("protocol_id", "protocol_start_date", "species_id"):
            self.initial[name] = definition["defaults"].get(name, "")

    def definition(self):
        result = deepcopy(self.draft.configuration)
        for key in ("date_order", "grouping"):
            result[key] = self.cleaned_data[key]
        result["identity_columns"] = [column_definition(self.table, value) for value in self.cleaned_data["identity_columns"]]
        for name in ("protocol_id", "species_id"):
            obj = self.cleaned_data[name]
            result["defaults"][name] = obj.pk if obj else ""
        start_date = self.cleaned_data["protocol_start_date"]
        result["defaults"]["protocol_start_date"] = start_date.isoformat() if start_date else ""
        result["defaults"].pop("marking_type", None)
        return result


class MappingForm(forms.Form):
    def __init__(self, draft, table, *args, **kwargs):
        self.require_identity = kwargs.pop("require_identity", True)
        super().__init__(*args, **kwargs)
        self.draft, self.table = draft, table
        definition = draft.configuration
        recording_form = RecordingForm(experiment=draft.experiment)
        value_fields = {key: recording_form.fields[key] for key in ("recording_date", "data_paths", "notes")}
        required_targets = {key for key, field in value_fields.items() if field.required}
        for name, recording_field in recording_form.dynamic_fields:
            key = f"field:{recording_field.key}"
            value_fields[key] = recording_form.fields[name]
            if recording_field.required:
                required_targets.add(key)
        value_fields.update({
            "animal:date_of_birth": forms.DateField(),
            "animal:cull_date": forms.DateField(),
            "animal:severity": forms.ChoiceField(choices=MAX_SEVERITY_CHOICES),
            "animal:sex": forms.ChoiceField(choices=Mouse.SEX_CHOICES),
            "animal:status": forms.ChoiceField(choices=Mouse.STATUS_CHOICES),
            "animal:notes": forms.CharField(widget=forms.Textarea()),
        })
        animal_requirements = {
            "animal:identifier": "Use the unique Animal ID when available. Otherwise leave blank.",
            "animal:tattoo": "Used when Animal ID is blank; new animals receive generated IDs.",
            "animal:crossing": "Required for new animals; can also be set in Crossings & genotypes.",
            "animal:date_of_birth": "Required when generating animal IDs.",
            "animal:breeding_pair": "Required when generating animal IDs.",
        }
        if self.is_bound:
            self.data = self.data.copy()
        self.mapping_rows = []
        for key, label, _ in targets(draft.experiment):
            column_name, default_name = f"column_{key}", f"default_{key}"
            mode_name = f"mode_{key}"
            self.fields[mode_name] = forms.ChoiceField(
                label=f"{label}: value source", choices=[("column", "Source column"), ("shared", "Shared value")],
                widget=forms.HiddenInput(attrs={"data-import-mapping-mode": "true"}),
            )
            self.fields[column_name] = forms.ChoiceField(required=False, label=f"{label}: source column", choices=column_choices(table))
            original_field = value_fields.get(key, forms.CharField())
            self.fields[default_name] = shared_value_field(original_field, label)
            self.initial[column_name] = column_initial(table, definition["bindings"].get(key))
            initial = definition["defaults"].get(key, "")
            if initial != "" and isinstance(original_field, forms.DateField):
                try:
                    initial = parse_date(initial, definition["date_order"])
                except forms.ValidationError:
                    pass
            elif isinstance(original_field, forms.BooleanField) and initial != "":
                token = str(initial).strip().casefold()
                if token in {"true", "yes", "1"}:
                    initial = "true"
                elif token in {"false", "no", "0"}:
                    initial = "false"
            elif isinstance(original_field, forms.ChoiceField) and initial != "":
                try:
                    initial = typed_value(initial, original_field, definition["date_order"])
                except forms.ValidationError:
                    pass
            self.initial[default_name] = initial
            self.initial[mode_name] = "column" if self.initial[column_name] != "" or initial == "" else "shared"
            if self.is_bound:
                mode = self.data.get(mode_name)
                if mode == "column":
                    self.data[default_name] = ""
                elif mode == "shared":
                    self.data[column_name] = ""
            self.mapping_rows.append({
                "label": label, "column": self[column_name], "default": self[default_name],
                "mode": self[mode_name],
                "animal": key.startswith("animal:"), "required": key in required_targets,
                "identity": key in {"animal:identifier", "animal:tattoo"},
                "requirement": animal_requirements.get(key, ""),
            })
        for row in self.mapping_rows:
            value = row["default"].value() if row["mode"].value() == "shared" else row["column"].value()
            row["associated"] = value not in (None, "") and (not isinstance(value, str) or bool(value.strip()))
        # Keep configured fields out of the way without moving rows during editing.
        self.mapping_rows.sort(key=lambda row: 2 if row["associated"] else 0 if row["required"] else 1)

    def clean(self):
        cleaned = super().clean()
        if self.require_identity and not any(cleaned.get(f"{prefix}_animal:{key}") for prefix in ("column", "default") for key in ("identifier", "tattoo")):
            self.add_error("column_animal:identifier", "Map an Animal ID or Local identifier, or supply a shared value.")
        return cleaned

    def definition(self):
        result = deepcopy(self.draft.configuration)
        for key, *_ in targets(self.draft.experiment):
            selected = column_definition(self.table, self.cleaned_data[f"column_{key}"])
            if selected:
                result["bindings"][key] = selected
            else:
                result["bindings"].pop(key, None)
            value = self.cleaned_data[f"default_{key}"]
            if value is None:
                value = ""
            elif hasattr(value, "isoformat"):
                value = value.isoformat()
            result["defaults"][key] = value
        return result


class AnimalsForm(forms.Form):
    def __init__(self, draft, table, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.draft = draft
        self.groups = []
        resolutions = {}
        for group in animal_groups(draft, table):
            key = group["key"]
            resolution = draft.state.get("animals", {}).get(key, initial_resolution(group, draft.configuration))
            resolutions[key] = resolution
            choices = [("link", "Link existing animal"), ("create", "Create with supplied ID")]
            if not group["animal_id"]:
                choices.append(("generate", "Create with generated ID"))
            fields = {
                "mode": forms.ChoiceField(choices=choices, label="Action", widget=forms.Select(attrs={"data-import-animal-action": ""})),
                "mouse_id": forms.CharField(required=False, label="Animal ID (unique)", max_length=100, widget=forms.TextInput(attrs={"data-import-animal-search": reverse("lab:mouse_lookup"), "autocomplete": "off"})),
                "date_of_birth": forms.DateField(required=False, label="Birth date override", widget=forms.DateInput(attrs={"type": "date"})),
                "tattoo": forms.CharField(required=False, max_length=100, label="Local identifier override (tattoo, ear clip)"),
                "breeding_pair": forms.CharField(required=False, max_length=100, label="Breeding pair override"),
                "sex": forms.ChoiceField(required=False, choices=[("", "Use mapped value"), *Mouse.SEX_CHOICES], label="Sex override"),
                "status": forms.ChoiceField(required=False, choices=[("", "Use mapped value / Alive for new animals"), *Mouse.STATUS_CHOICES], label="Status override"),
                "cull_date": forms.DateField(required=False, label="Cull date override", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})),
                "severity": forms.ChoiceField(required=False, choices=[("", "Use mapped value / leave unchanged"), *MAX_SEVERITY_CHOICES], label="Severity experienced override"),
                "protocol": forms.ModelChoiceField(queryset=Protocol.objects.all(), required=False, label="Protocol override"),
                "crossing_definition": forms.ModelChoiceField(queryset=Crossing.objects.filter(active=True).select_related("species"), required=False, label="Crossing override", widget=crossing_select()),
            }
            bound = []
            for name, field in fields.items():
                field_name = f"{key}_{name}"
                self.fields[field_name] = field
                self.initial[field_name] = resolution.get(name, "") if name in {"mode", "mouse_id"} else resolution.get("details", {}).get(name, "")
                if name == "mode" and resolution.get("mode") == "create" and resolution.get("generate_id"):
                    self.initial[field_name] = "generate"
                bound.append(self[field_name])
            candidates = Mouse.objects.filter(tattoo__iexact=group["identifier"]).select_related("crossing_definition")[:20] if not group["animal_id"] and group["identifier"] else []
            mapped = {}
            try:
                mapped = group_details(draft, table, group)
            except forms.ValidationError:
                pass
            self.groups.append({
                **group, "fields": bound, "candidates": list(candidates), "mapped": animal_display_details(mapped),
                "mode_field": bound[0], "mouse_id_field": bound[1],
                "override_fields": bound[2:], "has_overrides": any(resolution.get("details", {}).values()),
            })
        if not self.is_bound and any(resolution.get("generate_id") for resolution in resolutions.values()):
            provisional = copy(draft)
            provisional.state = {**draft.state, "animals": resolutions}
            try:
                previews = {animal["key"]: animal["mouse_id"] for animal in build_review(provisional)["animals"] if animal["generate_id"]}
                for group in self.groups:
                    group["proposed_id"] = previews.get(group["key"], "")
            except forms.ValidationError:
                pass
        if self.is_bound:
            for group in self.groups:
                group["overrides_open"] = any(field.errors for field in group["override_fields"])

    def clean(self):
        cleaned = super().clean()
        for group in self.groups:
            key = group["key"]
            if cleaned.get(f"{key}_mode") == "create" and not cleaned.get(f"{key}_mouse_id"):
                self.add_error(f"{key}_mouse_id", "Supply the unique Animal ID, or choose Create with generated ID.")
            if cleaned.get(f"{key}_mode") == "link":
                value = cleaned.get(f"{key}_mouse_id")
                matches = list(Mouse.objects.filter(pk__iexact=value)[:2]) if value else []
                if len(matches) != 1:
                    self.add_error(f"{key}_mouse_id", "Select one existing Animal ID.")
                else:
                    cleaned[f"{key}_mouse_id"] = matches[0].pk
        return cleaned

    def apply(self, draft):
        resolutions = {}
        for group in self.groups:
            key = group["key"]
            old = draft.state.get("animals", {}).get(key, {})
            action = self.cleaned_data[f"{key}_mode"]
            result = {"mode": "link" if action == "link" else "create", "mouse_id": self.cleaned_data.get(f"{key}_mouse_id", "") if action != "generate" else "", "generate_id": action == "generate"}
            details = {}
            for name in ("date_of_birth", "tattoo", "breeding_pair", "sex", "status", "cull_date", "severity", "protocol", "crossing_definition"):
                value = self.cleaned_data.get(f"{key}_{name}", "")
                if value is None:
                    value = ""
                elif hasattr(value, "pk"):
                    value = value.pk
                elif hasattr(value, "isoformat"):
                    value = value.isoformat()
                details[name] = value
            result["details"] = details
            result["genotypes"] = old.get("genotypes", {})
            resolutions[key] = result
        draft.state["animals"] = resolutions
        draft.state.pop("identifier_mapping_updated", None)


class ProceduresForm(forms.Form):
    procedure_drafts = forms.JSONField(required=False, initial=list, widget=forms.HiddenInput)

    def __init__(self, draft, table, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["procedure_drafts"] = draft.configuration.get("procedure_drafts", [])

    def clean_procedure_drafts(self):
        return procedure_definitions(self.cleaned_data.get("procedure_drafts"))

    def apply(self, draft):
        draft.configuration["procedure_drafts"] = self.cleaned_data["procedure_drafts"]


class GeneticsForm(forms.Form):
    crossing_id = forms.ModelChoiceField(queryset=Crossing.objects.filter(active=True).select_related("species"), required=False, label="Shared crossing override", widget=crossing_select(), help_text="Applies to new animals and proposes a change for existing animals; individual overrides take precedence.")

    def __init__(self, draft, table, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.draft, self.table = draft, table
        definition = draft.configuration
        self.initial["crossing_id"] = definition["defaults"].get("crossing_id", "")
        groups = animal_groups(draft, table)
        raw_crossings = sorted({str(mapped_value(definition, table, row, "animal:crossing")).strip() for group in groups for row in group["rows"]} - {""})
        self.crossing_rows, self.line_rows, self.term_rows, self.individual_rows = [], [], [], []
        for raw in raw_crossings:
            name = "crossing_" + digest(raw)[:16]
            self.fields[name] = forms.ModelChoiceField(queryset=Crossing.objects.filter(active=True).select_related("species"), required=False, label=raw, widget=crossing_select())
            candidates = crossing_candidates(raw, definition["defaults"].get("species_id"))
            suggestion = crossing_suggestion(raw, definition["defaults"].get("species_id"))
            self.initial[name] = definition.get("crossing_map", {}).get(raw, suggestion.pk if suggestion else "")
            self.crossing_rows.append({"raw": raw, "name": name, "field": self[name], "ambiguous": len(candidates) > 1})
        crossing_ids, group_crossings = [], {}
        for group in groups:
            resolution = draft.state.get("animals", {}).get(group["key"], {})
            selected = resolution.get("details", {}).get("crossing_definition")
            if not selected:
                selected = self.data.get("crossing_id") if self.is_bound else definition["defaults"].get("crossing_id")
            if not selected:
                raw = str(mapped_value(definition, table, group["rows"][0], "animal:crossing")).strip()
                name = "crossing_" + digest(raw)[:16]
                selected = self.data.get(name) if self.is_bound else self.initial.get(name)
            try:
                details = group_details(draft, table, group)
                selected = selected or details.get("crossing_definition")
            except (forms.ValidationError, ValueError):
                pass
            if not selected and resolution.get("mode") == "link":
                existing = Mouse.objects.filter(pk=resolution.get("mouse_id")).first()
                selected = existing.crossing_definition_id if existing else None
            if selected and str(selected).isdigit():
                if int(selected) not in crossing_ids:
                    crossing_ids.append(int(selected))
                group_crossings[group["key"]] = int(selected)
        crossing_lines = {
            crossing.pk: [line for line in crossing.ordered_lines() if line.active]
            for crossing in Crossing.objects.filter(pk__in=crossing_ids)
        }
        group_lines = {
            key: {line.pk for line in crossing_lines.get(pk, [])}
            for key, pk in group_crossings.items()
        }
        # Keep each crossing's stored membership order; shared lines appear once,
        # following the first crossing encountered in the selected source rows.
        lines = list({line.pk: line for pk in crossing_ids for line in crossing_lines.get(pk, [])}.values())
        active_categories = list(GenotypeCategory.objects.filter(active=True))
        category_choices = [(category.code, category.label) for category in active_categories]
        categories = [("", "Leave unchanged / Unknown for new animals"), *category_choices]
        term_sources = {}
        for line in lines:
            target = f"genotype:{line.pk}"
            column_name, default_name = f"genotype_column_{line.pk}", f"genotype_default_{line.pk}"
            self.fields[column_name] = forms.ChoiceField(required=False, label=f"{line.name}: source column", choices=column_choices(table))
            self.fields[default_name] = forms.ChoiceField(required=False, label=f"{line.name}: shared genotype", choices=categories)
            selected = column_initial(table, definition["bindings"].get(target))
            if not selected:
                # Prefer the actual line name over aliases or punctuation variants.
                matches = [c for c in table["columns"] if c["heading"].strip().casefold() == line.name.strip().casefold()]
                if not matches:
                    aliases = {normalize(line.name), *(normalize(a) for a in line.aliases.replace(";", ",").split(",") if a.strip())}
                    matches = [c for c in table["columns"] if normalize(c["heading"]) in aliases]
                if len(matches) == 1:
                    selected = str(matches[0]["index"])
            self.initial[column_name], self.initial[default_name] = selected, definition["defaults"].get(target, "")
            chosen_column = self.data.get(column_name, "") if self.is_bound else selected
            raw_values = set()
            if chosen_column and chosen_column.isdigit() and int(chosen_column) < len(table["columns"]):
                for group in groups:
                    if line.pk in group_lines.get(group["key"], set()):
                        override_name = f"individual_{group['key']}_{line.pk}"
                        override = self.data.get(override_name) if self.is_bound else draft.state.get("animals", {}).get(group["key"], {}).get("genotypes", {}).get(str(line.pk))
                        if override:
                            continue
                        raw_values.update(str(row["values"][int(chosen_column)]) for row in group["rows"] if row["values"][int(chosen_column)] != "")
            for raw in sorted(raw_values):
                key = genotype_term_key(raw)
                if key:
                    term_sources.setdefault(key, set()).add((line.pk, raw))
            self.line_rows.append({"line": line, "column_name": column_name, "default_name": default_name, "column": self[column_name], "default": self[default_name]})
        for key, uses in sorted(term_sources.items()):
            resolved = {resolve_genotype_term(definition, raw, line_id, categories=active_categories) for line_id, raw in uses}
            if len(resolved) == 1 and None not in resolved:
                continue
            variants = sorted({raw.strip() for _, raw in uses})
            name = f"term_{digest(key)[:16]}"
            self.fields[name] = forms.ChoiceField(required=False, label=f"{' / '.join(variants)} means", choices=[("", "Select a genotype category"), *category_choices])
            known_codes = resolved - {None}
            if len(known_codes) == 1:
                self.initial[name] = next(iter(known_codes))
            elif not known_codes:
                self.initial[name] = genotype_suggestion(variants[0])
            else:
                self.initial[name] = ""
            self.term_rows.append({"key": key, "variants": variants, "name": name, "field": self[name]})
        for group in groups:
            values = draft.state.get("animals", {}).get(group["key"], {}).get("genotypes", {})
            fields = []
            for line in crossing_lines.get(group_crossings.get(group["key"]), []):
                name = f"individual_{group['key']}_{line.pk}"
                self.fields[name] = forms.ChoiceField(required=False, label=line.name, choices=categories)
                self.initial[name] = values.get(str(line.pk), "")
                fields.append({"name": name, "line_id": str(line.pk), "field": self[name]})
            self.individual_rows.append({"group": group, "fields": fields})

    def clean(self):
        cleaned = super().clean()
        if self.data.get("next_step") == "review":
            for row in self.term_rows:
                if not cleaned.get(row["name"]):
                    self.add_error(row["name"], "Resolve this term before reviewing the import.")
        return cleaned

    def apply(self, draft):
        crossing = self.cleaned_data["crossing_id"]
        draft.configuration["defaults"]["crossing_id"] = crossing.pk if crossing else ""
        for row in self.crossing_rows:
            crossing = self.cleaned_data[row["name"]]
            draft.configuration["crossing_map"][row["raw"]] = crossing.pk if crossing else ""
        for row in self.line_rows:
            target = f"genotype:{row['line'].pk}"
            selected = column_definition(self.table, self.cleaned_data[row["column_name"]])
            if selected:
                draft.configuration["bindings"][target] = selected
            else:
                draft.configuration["bindings"].pop(target, None)
            draft.configuration["defaults"][target] = self.cleaned_data[row["default_name"]]
        terms = draft.configuration.setdefault("genotype_terms", {})
        for row in self.term_rows:
            code = self.cleaned_data[row["name"]]
            if code:
                for term in list(terms):
                    if genotype_term_key(term) == row["key"]:
                        del terms[term]
                terms[row["key"]] = code
                for legacy_map in draft.configuration.get("genotype_maps", {}).values():
                    for term in list(legacy_map):
                        if genotype_term_key(term) == row["key"]:
                            del legacy_map[term]
        for row in self.individual_rows:
            resolution = draft.state.get("animals", {}).get(row["group"]["key"])
            if resolution is not None:
                resolution["genotypes"] = {f["line_id"]: self.cleaned_data[f["name"]] for f in row["fields"] if self.cleaned_data[f["name"]]}
