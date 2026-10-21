"""Shared form formats, widgets and choice normalization."""

from django import forms


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
    "Optional tattoo, ear clip or other local identifier. Leave blank for unmarked mice."
)


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
