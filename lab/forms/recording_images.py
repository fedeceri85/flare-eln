from django import forms

class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        if not data:
            return super().clean(data, initial)
        return [super(MultipleFileField, self).clean(item, initial) for item in (data if isinstance(data, (list, tuple)) else [data])]


class RecordingImageUploadForm(forms.Form):
    images = MultipleFileField(label="Images", widget=MultipleFileInput(attrs={"accept": "image/jpeg,image/png,image/webp,image/tiff,.tif,.tiff"}),
        help_text="JPEG, PNG, WebP or TIFF. Up to 10 at a time, 20 MB each. Stored as compressed viewing copies. Multipage TIFFs use the first page.")

    def __init__(self, *args, optional=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["images"].required = not optional
        self.fields["images"].widget.attrs["data-media-input"] = "true"


class RecordingDocumentUploadForm(forms.Form):
    documents = MultipleFileField(
        label="PDF documents",
        widget=MultipleFileInput(attrs={"accept": "application/pdf,.pdf"}),
        help_text="PDF only. Up to 10 at a time, 10 MB each.",
    )

    def __init__(self, *args, optional=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["documents"].required = not optional


class RecordingMediaUploadForm(RecordingImageUploadForm):
    documents = MultipleFileField(
        label="PDF documents",
        widget=MultipleFileInput(attrs={"accept": "application/pdf,.pdf"}),
        help_text="PDF only. Up to 10 at a time, 10 MB each.",
        required=False,
    )
