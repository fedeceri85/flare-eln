from django import forms

from ..recording_images import configured_directory, validate_directory


class ImageStorageForm(forms.Form):
    directory = forms.CharField(max_length=1024, label="Folder for new images",
        help_text="Use an existing folder writable by the application. Existing images stay in their original folders.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["directory"].initial = str(configured_directory())

    def clean_directory(self):
        return validate_directory(self.cleaned_data["directory"])


class MultipleImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    widget = MultipleImageInput

    def clean(self, data, initial=None):
        if not data:
            return super().clean(data, initial)
        return [super(MultipleImageField, self).clean(item, initial) for item in (data if isinstance(data, (list, tuple)) else [data])]


class RecordingImageUploadForm(forms.Form):
    images = MultipleImageField(label="Images", widget=MultipleImageInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        help_text="JPEG, PNG or WebP. Up to 10 at a time, 20 MB each. Stored as compressed viewing copies.")
    caption = forms.CharField(required=False, max_length=500, label="Caption (optional)",
        help_text="Applies to the selected images. You can edit each caption afterward.")

    def __init__(self, *args, optional=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["images"].required = not optional
        self.fields["images"].widget.attrs["data-media-input"] = "true"
