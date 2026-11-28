"""Form for configuring the AI assistant provider connection."""
import re

from django import forms

from .models import AIProviderSettings

# The assistant name is a display label, not prompt material. Keep it name-like
# so it cannot smuggle line breaks or arbitrary text into the system prompt.
_ASSISTANT_NAME_RE = re.compile(r"^[\w .'\-()]+$")


def validate_assistant_name(value: str) -> None:
    """Reject assistant names that are not short, single-line labels."""
    value = (value or "").strip()
    if not value:
        return
    if any(ch.isspace() and ch != " " for ch in value):
        raise forms.ValidationError("The assistant name must not contain line breaks.")
    if not _ASSISTANT_NAME_RE.match(value):
        raise forms.ValidationError(
            "Use letters, numbers, spaces, hyphens, apostrophes and dots only "
            "(e.g. Marcott-E). The name must not contain instructions or sentences."
        )


class AIProviderSettingsForm(forms.Form):
    base_url = forms.CharField(
        label="Base URL",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "http://model-server:1234/v1"}),
    )
    api_key = forms.CharField(
        label="API key",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the current key.",
    )
    model_name = forms.CharField(
        label="Model",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "qwen/qwen3.5-9b"}),
    )
    assistant_name = forms.CharField(
        label="Assistant name",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={"placeholder": "Marcott-E"}),
        validators=[validate_assistant_name],
    )

    def __init__(self, *args, **kwargs):
        self.instance = kwargs.pop("instance", None)
        super().__init__(*args, **kwargs)
        # Pre-fill non-secret fields from the current settings.
        if self.instance:
            self.initial["base_url"] = self.instance.base_url
            self.initial["model_name"] = self.instance.model_name
            self.initial["assistant_name"] = self.instance.assistant_name
            if self.instance.has_api_key:
                self.fields["api_key"].widget.attrs["placeholder"] = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022 (stored \u2014 leave blank to keep)"
        # Never pre-fill the API key value.

    def save(self):
        instance = self.instance or AIProviderSettings.singleton()
        instance.base_url = self.cleaned_data["base_url"].strip()
        instance.model_name = self.cleaned_data["model_name"].strip()
        instance.assistant_name = self.cleaned_data["assistant_name"].strip()
        # API key: blank means "keep current", non-blank means "replace".
        new_key = self.cleaned_data["api_key"].strip()
        if new_key:
            instance.api_key = new_key
        instance.save()
        return instance