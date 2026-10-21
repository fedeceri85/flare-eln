"""Individual mouse creation, editing, identity and deletion forms."""

from django import forms

from ..models import Mouse, Protocol
from .common import (
    DATE_WIDGETS,
    LOCAL_IDENTIFIER_HELP_TEXT,
    LOCAL_IDENTIFIER_LABEL,
)
from .genetics import MouseStructuredGeneticsMixin


MOUSE_EDIT_FIELDS = [
    "date_of_birth",
    "marking_type",
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
            "marking_type",
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
        self.instance.id_status = Mouse.ID_TEMPORARY if generate_mouse_id else Mouse.ID_ASSIGNED

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

    def save(self, commit=True):
        self.instance.id_status = (
            Mouse.ID_TEMPORARY if self.cleaned_data.get("generate_mouse_id") else Mouse.ID_ASSIGNED
        )
        return super().save(commit=commit)


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
