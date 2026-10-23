"""Individual and bulk procedure creation and editing forms."""

from django import forms

from ..models import Procedure, ProcedureType
from .common import HTML_DATE_FORMAT


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
            "date": forms.DateInput(
                format=HTML_DATE_FORMAT,
                attrs={"type": "date"},
            ),
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
                "This always adds a new procedure row for the animal."
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


class BulkProcedureCreateForm(BaseProcedureForm):
    def __init__(self, *args, **kwargs):
        kwargs["prefix"] = "new_procedure"
        super().__init__(*args, **kwargs)


class ExistingProcedureBulkForm(forms.Form):
    selected_procedures = forms.ModelMultipleChoiceField(
        queryset=Procedure.objects.none(), widget=forms.MultipleHiddenInput,
        error_messages={"invalid_choice": "One or more procedures are no longer available in this animal selection."},
    )
    snapshot = forms.CharField(required=False, widget=forms.HiddenInput)
    update_procedure_type = forms.BooleanField(required=False, label="Update procedure type")
    procedure_type = forms.ModelChoiceField(queryset=ProcedureType.objects.all(), required=False)
    update_date = forms.BooleanField(required=False, label="Update date")
    date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}, format=HTML_DATE_FORMAT))
    update_lab_member = forms.BooleanField(required=False, label="Update lab member")
    lab_member = forms.CharField(required=False, max_length=100)
    update_lab_book_page = forms.BooleanField(required=False, label="Update lab-book page")
    lab_book_page = forms.CharField(required=False, max_length=50, label="Lab-book page")
    editable_fields = ("procedure_type", "date", "lab_member", "lab_book_page")

    def __init__(self, *args, procedures, deleting=False, **kwargs):
        self.deleting = deleting
        kwargs["prefix"] = "existing_procedures"
        super().__init__(*args, **kwargs)
        self.fields["selected_procedures"].queryset = procedures
        for name in self.editable_fields:
            self.fields[name].disabled = deleting or not (self.is_bound and self.data.get(self.add_prefix(f"update_{name}")))

    @property
    def update_fields(self):
        return [{"checkbox": self[f"update_{name}"], "field": self[name]} for name in self.editable_fields]

    def clean(self):
        data = super().clean()
        if self.deleting:
            return data
        if not any(data.get(f"update_{name}") for name in self.editable_fields):
            raise forms.ValidationError("Choose at least one field to update.")
        if data.get("update_procedure_type") and not data.get("procedure_type"):
            self.add_error("procedure_type", "Select a valid procedure type.")
        return data

    def updates(self):
        result = {}
        for name in self.editable_fields:
            if self.cleaned_data.get(f"update_{name}"):
                value = self.cleaned_data[name]
                result["procedure_type_id" if name == "procedure_type" else name] = value.pk if name == "procedure_type" else value
        return result
