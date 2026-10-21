"""Public form API, backed by feature-specific modules.

Existing ``from lab.forms import ...`` imports remain supported. Feature modules
import their dependencies directly, rather than depending on this facade.
"""

from .bulk_mice import (
    BulkMouseUpdateForm,
)

from .common import (
    HTML_DATE_FORMAT,
    DATE_WIDGETS,
    ENGLISH_DATE_FORMAT,
    HOME_OFFICE_DATE_INPUT_FORMATS,
    LOCAL_IDENTIFIER_LABEL,
    LOCAL_IDENTIFIER_HELP_TEXT,
    normalize_choice_values,
)

from .genetics import (
    active_crossings_queryset,
    crossing_choice_label,
    individual_mouse_field_prefix,
    genotype_field_name,
    truncate_compat_text,
    genotype_summary,
    visible_field_sections_for_names,
    sync_mouse_structured_genetics,
    BulkMouseLineCreateForm,
    CrossingCreateForm,
    MouseStructuredGeneticsMixin,
)

from .litter import (
    LitterCreateForm,
)

from .mice import (
    MOUSE_EDIT_FIELDS,
    MouseForm,
    MouseEditForm,
    MouseRenameForm,
    MouseDeleteConfirmForm,
    BulkMouseDeleteConfirmForm,
)

from .procedures import (
    BaseProcedureForm,
    ProcedureCreateForm,
    ProcedureEditForm,
    BulkProcedureCreateForm,
    ExistingProcedureBulkForm,
)

from .projects import (
    ProjectForm,
    ExperimentForm,
)

from .recordings import (
    parse_recording_paths,
    recording_type_choices_for_instance,
    RecordingForm,
)

from .reporting import (
    HomeOfficeReturnForm,
)

__all__ = [
    "BaseProcedureForm",
    "BulkMouseDeleteConfirmForm",
    "BulkMouseLineCreateForm",
    "BulkMouseUpdateForm",
    "BulkProcedureCreateForm",
    "CrossingCreateForm",
    "DATE_WIDGETS",
    "ENGLISH_DATE_FORMAT",
    "ExistingProcedureBulkForm",
    "ExperimentForm",
    "HOME_OFFICE_DATE_INPUT_FORMATS",
    "HTML_DATE_FORMAT",
    "HomeOfficeReturnForm",
    "LOCAL_IDENTIFIER_HELP_TEXT",
    "LOCAL_IDENTIFIER_LABEL",
    "LitterCreateForm",
    "MOUSE_EDIT_FIELDS",
    "MouseDeleteConfirmForm",
    "MouseEditForm",
    "MouseForm",
    "MouseRenameForm",
    "MouseStructuredGeneticsMixin",
    "ProcedureCreateForm",
    "ProcedureEditForm",
    "ProjectForm",
    "RecordingForm",
    "active_crossings_queryset",
    "crossing_choice_label",
    "genotype_field_name",
    "genotype_summary",
    "individual_mouse_field_prefix",
    "normalize_choice_values",
    "parse_recording_paths",
    "recording_type_choices_for_instance",
    "sync_mouse_structured_genetics",
    "truncate_compat_text",
    "visible_field_sections_for_names",
]
