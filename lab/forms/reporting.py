"""Home Office return input and date validation."""

from datetime import date

from django import forms
from django.utils import timezone

from ..models import Protocol
from .common import ENGLISH_DATE_FORMAT, HOME_OFFICE_DATE_INPUT_FORMATS


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
