"""Form for configuring the AI assistant provider connection."""
from django import forms

from .models import AIProviderSettings


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
        widget=forms.TextInput(attrs={"placeholder": "Marcott-E"}),
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