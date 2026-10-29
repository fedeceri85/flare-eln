from datetime import date, timedelta
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower, Trim
from django.utils import timezone


def import_draft_expiry():
    return timezone.now() + timedelta(hours=24)


class SpreadsheetImportDraft(models.Model):
    """Reusable mapping rules and file-specific state deliberately live apart."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    experiment = models.ForeignKey("Experiment", on_delete=models.CASCADE)
    filename = models.CharField(max_length=255)
    source = models.JSONField(default=list)
    configuration = models.JSONField(default=dict)
    state = models.JSONField(default=dict)
    expires_at = models.DateTimeField(default=import_draft_expiry, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SpreadsheetImportTemplate(models.Model):
    """Shared column associations, independent of any uploaded file or draft."""

    recording_type = models.ForeignKey("RecordingType", on_delete=models.CASCADE, related_name="import_templates")
    name = models.CharField(max_length=200)
    mapping = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["recording_type__name", "name", "pk"]
        constraints = [
            models.UniqueConstraint(
                models.F("recording_type"), Lower(Trim("name")),
                name="unique_import_template_type_name",
            ),
        ]

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

MAX_SEVERITY_CHOICES = [
    ("Non-recovery", "Non-recovery"),
    ("Mild", "Mild"),
    ("Moderate", "Moderate"),
    ("Severe", "Severe"),
    ]
SEVERITY_RANK = {
            "Non-recovery": 0,
            "Mild": 1,
            "Moderate": 2,
            "Severe": 3,
        }

class Protocol(models.Model):
    protocol_id = models.AutoField(primary_key=True)
    protocol_number = models.IntegerField()
    licence_reference = models.CharField(max_length=100)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    allows_regulated_procedures = models.BooleanField(
        default=True,
        verbose_name="Allows regulated procedures",
    )

    max_severity = models.CharField(
        max_length=15,
        choices=MAX_SEVERITY_CHOICES,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["licence_reference", "protocol_number"],
                name="unique_protocol_number_per_licence",
            ),
        ]

    def __str__(self):
        return f"{self.name}"
    

DEFAULT_SPECIES_NAME = "Mouse"
MAX_CROSSING_LINES = 5


class GenotypeCategory(models.Model):
    code = models.SlugField(max_length=20, unique=True)
    label = models.CharField(max_length=50, unique=True)
    position = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "label"]
        verbose_name_plural = "Genotype categories"

    def __str__(self):
        return self.label

    def clean(self):
        super().clean()
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            if self.code != original.code:
                raise ValidationError({"code": "Stored category codes cannot be changed."})
        if self.code == "unknown" and not self.active:
            raise ValidationError({"active": "Unknown must remain available as the default."})


def genotype_category_choices(include_codes=()):
    return list(
        GenotypeCategory.objects.filter(
            models.Q(active=True) | models.Q(code__in=include_codes)
        ).values_list("code", "label")
    )


def genotype_category_labels():
    return dict(GenotypeCategory.objects.values_list("code", "label"))


class Species(models.Model):
    species_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Species"
        verbose_name_plural = "Species"

    def __str__(self):
        return self.name


class MouseLine(models.Model):
    mouse_line_id = models.AutoField(primary_key=True)
    species = models.ForeignKey(
        Species,
        on_delete=models.PROTECT,
        related_name="lines",
    )
    name = models.CharField(max_length=200)
    aliases = models.TextField(blank=True, default="")
    is_wild_type = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["species__name", "name"]
        constraints = [
            models.UniqueConstraint(
                "species",
                Lower("name"),
                name="unique_mouse_line_name_per_species",
            ),
        ]

    def __str__(self):
        return f"{self.species} - {self.name}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        ensure_single_line_crossing(self)


class Crossing(models.Model):
    WT = "WT"
    SINGLE = "Single"
    MULTIPLE = "Multiple"

    crossing_id = models.AutoField(primary_key=True)
    species = models.ForeignKey(
        Species,
        on_delete=models.PROTECT,
        related_name="crossings",
    )
    display_name = models.CharField(max_length=500)
    canonical_key = models.CharField(max_length=300)
    active = models.BooleanField(default=True)
    lines = models.ManyToManyField(
        MouseLine,
        through="CrossingLine",
        related_name="crossings",
        blank=True,
    )

    class Meta:
        ordering = ["species__name", "display_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["species", "canonical_key"],
                name="unique_crossing_canonical_key_per_species",
            ),
        ]

    def __str__(self):
        return f"{self.species} - {self.display_name}"

    def ordered_lines(self):
        return [
            membership.line
            for membership in self.crossing_lines.select_related("line").order_by(
                "position", "crossing_line_id"
            )
        ]

    @property
    def inferred_crossing_type(self):
        lines = list(self.lines.all())

        if not lines or all(line.is_wild_type for line in lines):
            return self.WT

        mutant_line_count = sum(not line.is_wild_type for line in lines)

        if mutant_line_count == 1:
            return self.SINGLE

        return self.MULTIPLE


class Mouse(models.Model):
    date_added = models.DateField(
        default=timezone.localdate,
        null=True,
        blank=True,
        editable=False,
        verbose_name="Date added",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="added_mice",
        verbose_name="Added by",
    )
    mouse_id = models.CharField(max_length=100, primary_key=True)
    mother_external_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    mother = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="maternal_offspring")
    father_external_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    father = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="paternal_offspring")
    ID_TEMPORARY = "temporary"
    ID_ASSIGNED = "assigned"
    ID_UNKNOWN = "unknown"
    ID_STATUS_CHOICES = [
        (ID_TEMPORARY, "Temporary"), (ID_ASSIGNED, "Assigned"), (ID_UNKNOWN, "Unknown"),
    ]
    id_status = models.CharField(
        max_length=10, choices=ID_STATUS_CHOICES, default=ID_UNKNOWN,
        editable=False, verbose_name="Mouse ID status",
    )
    date_of_birth = models.DateField(null=True, blank=True)
    MARKING_CHOICES = [
        ("other", "Other"), ("tattoo", "Tattoo"), ("ear_clip", "Ear clip"),
    ]
    marking_type = models.CharField(max_length=10, choices=MARKING_CHOICES, default="other", verbose_name="Local ID type")
    tattoo = models.CharField(max_length=100, blank=True, verbose_name="Local identifier")
    genotype = models.CharField(max_length=100, blank=True)
    crossing = models.CharField(max_length=100, blank=True)
    breeding_pair = models.CharField(max_length=100, blank=True)

    CROSSING_CHOICES = [
        ("WT", "Wild Type"),
        ("Single", "Single Mutation"),
        ("Multiple", "Multiple"),
    ]
    crossing_type = models.CharField(
        max_length=20,
        choices=CROSSING_CHOICES,
        blank=True,
    )
    crossing_definition = models.ForeignKey(
        Crossing,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="mice",
    )

    SEX_CHOICES = [
        ("M", "Male"),
        ("F", "Female"),
    ]

    STATUS_CHOICES = [
        ("Alive", "Alive"),
        ("Culled", "Culled"),
    ]
    sex = models.CharField(
        max_length=1,
        choices=SEX_CHOICES,
        blank=True,
    )
    severity = models.CharField(
        max_length=15,
        choices=MAX_SEVERITY_CHOICES,
        blank=True,
    )

    @property
    def severity_exceeded(self):
        if not self.protocol or not self.severity or not self.protocol.max_severity:
            return False

        return (
            SEVERITY_RANK.get(self.severity, 0)
            > SEVERITY_RANK.get(self.protocol.max_severity, 0)
        )
    
    notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=50,
        choices=STATUS_CHOICES,
        blank=False,
        default="Alive",
    )
    protocol_start_date = models.DateField(null=True, blank=True)
    cull_date = models.DateField(null=True, blank=True)
    custom_metadata = models.JSONField(default=dict, blank=True)
    protocol = models.ForeignKey(Protocol,on_delete=models.PROTECT,null=False,blank=False,related_name="mice")

    class Meta:
        verbose_name = "Mouse"
        verbose_name_plural = "Mice"
        indexes = [
            models.Index(fields=["date_of_birth", "crossing_definition"], name="mouse_birth_crossing_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "date_of_birth",
                    "breeding_pair",
                    "crossing_definition",
                    "tattoo",
                    "marking_type",
                ],
                condition=~models.Q(tattoo="") & models.Q(id_status__in=["temporary", "unknown"]),
                name="unique_mouse_birth_pair_crossing_marking",
            ),
        ]

    def __str__(self):
            return self.mouse_id

    def clean(self):
        super().clean()
        if self.mother_id or self.father_id:
            from .parentage import validate_mouse_parentage
            validate_mouse_parentage(self)

    @property
    def genetics_crossing_display(self):
        if self.crossing_definition_id:
            return self.crossing_definition.display_name

        return self.crossing

    @property
    def genetics_crossing_type_display(self):
        if self.crossing_definition_id:
            labels = dict(self.CROSSING_CHOICES)
            return labels.get(
                self.crossing_definition.inferred_crossing_type,
                "",
            )

        if self.crossing_type == "Double_triple":
            return "Multiple"

        return self.get_crossing_type_display()

    @property
    def genetics_genotype_display(self):
        if not self.crossing_definition_id:
            return self.genotype

        calls_by_line = {
            call.mouse_line_id: call
            for call in self.genotype_calls.select_related("mouse_line")
        }
        genotype_calls = [
            calls_by_line[line.pk]
            for line in self.crossing_definition.ordered_lines()
            if line.pk in calls_by_line
        ]

        labels = genotype_category_labels()
        return "; ".join(
            f"{call.mouse_line.name}: {labels.get(call.zygosity, call.zygosity)}"
            + (" (uncertain)" if call.is_uncertain else "")
            for call in genotype_calls
        )


class CrossingLine(models.Model):
    crossing_line_id = models.AutoField(primary_key=True)
    crossing = models.ForeignKey(
        Crossing,
        on_delete=models.CASCADE,
        related_name="crossing_lines",
    )
    line = models.ForeignKey(
        MouseLine,
        on_delete=models.PROTECT,
        related_name="crossing_lines",
    )

    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["crossing", "position", "crossing_line_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["crossing", "line"],
                name="unique_line_per_crossing",
            ),
        ]

    def clean(self):
        super().clean()

        if (
            self.crossing_id
            and self.line_id
            and self.crossing.species_id != self.line.species_id
        ):
            raise ValidationError(
                "All lines in a crossing must belong to the crossing species."
            )

        if self.crossing_id:
            line_count = (
                type(self).objects
                .filter(crossing=self.crossing)
                .exclude(pk=self.pk)
                .count()
            )

            if line_count >= MAX_CROSSING_LINES:
                raise ValidationError(
                    f"A crossing can include at most {MAX_CROSSING_LINES} lines."
                )

    def __str__(self):
        return f"{self.crossing} - {self.line.name}"


class LoginEvent(models.Model):
    LOGIN = "login"
    LOGOUT = "logout"
    FAILED = "failed"

    EVENT_CHOICES = [
        (LOGIN, "Login"),
        (LOGOUT, "Logout"),
        (FAILED, "Failed login"),
    ]

    login_event_id = models.AutoField(primary_key=True)
    event_type = models.CharField(max_length=20, choices=EVENT_CHOICES)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="login_events",
    )
    username = models.CharField(max_length=150, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["-created_at"],
                name="login_event_created_at_idx",
            ),
            models.Index(
                fields=["event_type", "-created_at"],
                name="login_event_type_created_idx",
            ),
            models.Index(
                fields=["username"],
                name="login_event_username_idx",
            ),
        ]

    def __str__(self):
        return f"{self.get_event_type_display()} - {self.username or 'unknown'}"


class MouseGenotype(models.Model):
    mouse_genotype_id = models.AutoField(primary_key=True)
    mouse = models.ForeignKey(
        Mouse,
        on_delete=models.CASCADE,
        related_name="genotype_calls",
    )
    mouse_line = models.ForeignKey(
        MouseLine,
        on_delete=models.PROTECT,
        related_name="genotype_calls",
    )
    zygosity = models.CharField(
        max_length=20,
        default="unknown",
    )

    is_uncertain = models.BooleanField(default=False)

    def get_zygosity_display(self):
        label = genotype_category_labels().get(self.zygosity, self.zygosity)
        return f"{label} (uncertain)" if self.is_uncertain else label

    def clean(self):
        super().clean()
        if not GenotypeCategory.objects.filter(code=self.zygosity).exists():
            raise ValidationError({"zygosity": "Select a valid genotype category."})

    class Meta:
        ordering = ["mouse", "mouse_line__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["mouse", "mouse_line"],
                name="unique_genotype_call_per_mouse_line",
            ),
        ]

    def __str__(self):
        return (
            f"{self.mouse_id} - {self.mouse_line.name}: "
            f"{self.get_zygosity_display()}"
        )


def crossing_canonical_key(lines):
    return "|".join(
        str(line.pk)
        for line in sorted(lines, key=lambda line: line.pk)
    )


def crossing_display_name(lines):
    return " x ".join(
        line.name
        for line in lines
    )


def resolve_crossing(species, lines):
    normalized_lines = []
    seen_line_ids = set()

    for line in lines:
        if line.pk in seen_line_ids:
            continue

        seen_line_ids.add(line.pk)
        normalized_lines.append(line)

    if not normalized_lines:
        raise ValidationError("Select at least one line for the crossing.")

    if len(normalized_lines) > MAX_CROSSING_LINES:
        raise ValidationError(
            f"A crossing can include at most {MAX_CROSSING_LINES} lines."
        )

    mismatched_lines = [
        line.name
        for line in normalized_lines
        if line.species_id != species.pk
    ]

    if mismatched_lines:
        raise ValidationError(
            "All selected lines must belong to the selected species."
        )

    canonical_key = crossing_canonical_key(normalized_lines)
    crossing, created = Crossing.objects.get_or_create(
        species=species,
        canonical_key=canonical_key,
        defaults={
            "display_name": crossing_display_name(normalized_lines),
            "active": True,
        },
    )

    if created:
        CrossingLine.objects.bulk_create(
            [
                CrossingLine(crossing=crossing, line=line, position=position)
                for position, line in enumerate(normalized_lines)
            ]
        )

    return crossing, created


def ensure_single_line_crossing(line):
    crossing, created = resolve_crossing(line.species, [line])
    display_name = crossing_display_name([line])

    if crossing.display_name != display_name:
        crossing.display_name = display_name
        crossing.save(update_fields=["display_name"])

    return crossing, created


class ProcedureType(models.Model):
    procedure_type_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = "Procedure type"
        verbose_name_plural = "Procedure types"

    def __str__(self):
        return self.name


class Procedure(models.Model):
    procedure_id = models.AutoField(primary_key=True)

    date = models.DateField(null=True, blank=True)
    lab_member = models.CharField(max_length=100, blank=True)
    lab_book_page = models.CharField(max_length=50, blank=True)

    mouse = models.ForeignKey(
        Mouse,
        on_delete=models.CASCADE,
        related_name="procedures",
    )

    procedure_type = models.ForeignKey(
        ProcedureType,
        on_delete=models.PROTECT,
        related_name="procedures",
    )

    notes = models.TextField(blank=True)
    def __str__(self):
        return f"{self.mouse} - {self.procedure_type}"


SERVICES_PROJECT_NAME = "Services"
SERVICES_PROJECT_DESCRIPTION = "Lab-wide service experiments and shared records."


class Project(models.Model):
    project_id = models.AutoField(primary_key=True)

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    lab_member = models.CharField(max_length=100, blank=True)

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    active = models.BooleanField(default=True)
    is_service = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Experiment(models.Model):
    experiment_id = models.AutoField(primary_key=True)

    project = models.ForeignKey(
        Project,
        on_delete=models.PROTECT,
        related_name="experiments",
    )

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    recording_type = models.ForeignKey(
        "RecordingType",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="experiments",
    )

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["project", "name"]

    def __str__(self):
        return f"{self.project} - {self.name}"

    def clean(self):
        super().clean()

        if not self.pk:
            return

        existing_recording_type_id = (
            type(self).objects
            .filter(pk=self.pk)
            .values_list("recording_type_id", flat=True)
            .first()
        )

        if existing_recording_type_id == self.recording_type_id:
            return

        if self.recordings.exists():
            raise ValidationError(
                {
                    "recording_type": (
                        "Experiment type cannot be changed after recordings "
                        "have been added."
                    ),
                }
            )


def validate_recording_field_value(
    data_type,
    value,
    choices=None,
    required=False,
    label="Value",
):
    if value is None or value == "":
        if required:
            raise ValidationError(f"{label} is required.")

        return

    if data_type == RecordingField.TEXT:
        if not isinstance(value, str):
            raise ValidationError(f"{label} must be text.")
    elif data_type == RecordingField.INTEGER:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(f"{label} must be an integer.")
    elif data_type == RecordingField.FLOAT:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(f"{label} must be a number.")
    elif data_type == RecordingField.DATE:
        if isinstance(value, date):
            return

        if not isinstance(value, str):
            raise ValidationError(f"{label} must be an ISO date string.")

        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError(
                f"{label} must be an ISO date string."
            ) from exc
    elif data_type == RecordingField.BOOLEAN:
        if not isinstance(value, bool):
            raise ValidationError(f"{label} must be true or false.")
    elif data_type == RecordingField.CHOICE:
        if choices and value not in choices:
            raise ValidationError(f"{label} must be one of the allowed choices.")


class RecordingType(models.Model):
    recording_type_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=200)
    plural_name = models.CharField(max_length=200, blank=True)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    hide_data_paths_in_table = models.BooleanField(default=False)
    hide_notes_in_table = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def display_plural_name(self):
        return self.plural_name or f"{self.name}s"


class RecordingField(models.Model):
    TEXT = "text"
    INTEGER = "integer"
    FLOAT = "float"
    DATE = "date"
    BOOLEAN = "boolean"
    CHOICE = "choice"

    DATA_TYPE_CHOICES = [
        (TEXT, "Text"),
        (INTEGER, "Integer"),
        (FLOAT, "Float"),
        (DATE, "Date"),
        (BOOLEAN, "Boolean"),
        (CHOICE, "Choice"),
    ]

    recording_field_id = models.AutoField(primary_key=True)
    recording_type = models.ForeignKey(
        RecordingType,
        on_delete=models.CASCADE,
        related_name="fields",
    )
    key = models.SlugField()
    label = models.CharField(max_length=200)
    data_type = models.CharField(max_length=20, choices=DATA_TYPE_CHOICES)
    required = models.BooleanField(default=False)
    choices = models.JSONField(default=list, blank=True)
    units = models.CharField(max_length=50, blank=True)
    default_value = models.JSONField(null=True, blank=True)
    active = models.BooleanField(default=True)
    display_in_table = models.BooleanField(default=True)
    filterable = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["recording_type", "order", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["recording_type", "key"],
                name="unique_recording_field_key_per_type",
            ),
        ]

    def __str__(self):
        return f"{self.recording_type} - {self.label}"

    def clean(self):
        super().clean()

        if self.default_value is not None and self.default_value != "":
            validate_recording_field_value(
                self.data_type,
                self.default_value,
                choices=self.choices,
                label="Default value",
            )

        if not self.pk:
            return

        existing_field = type(self).objects.get(pk=self.pk)
        if existing_field.key == self.key:
            return

        has_existing_values = Recording.objects.filter(
            recording_type=existing_field.recording_type,
            values__has_key=existing_field.key,
        ).exists()

        if has_existing_values:
            raise ValidationError(
                {
                    "key": (
                        "Field keys cannot be changed after recordings "
                        "contain values for that key."
                    ),
                }
            )


class Recording(models.Model):
    recording_id = models.AutoField(primary_key=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recordings",
    )
    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.PROTECT,
        related_name="recordings",
    )
    mice = models.ManyToManyField(
        Mouse,
        blank=True,
        related_name="recordings",
    )
    recording_type = models.ForeignKey(
        RecordingType,
        on_delete=models.PROTECT,
        related_name="recordings",
    )
    recording_date = models.DateField()
    data_paths = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    values = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-recording_date", "recording_id"]

    def __str__(self):
        return (
            f"{self.mice_label} | "
            f"{self.recording_date} | "
            f"{self.experiment.name}"
        )

    @property
    def mice_label(self):
        if not self.pk:
            return "No mice"

        mouse_ids = [
            mouse.mouse_id
            for mouse in sorted(
                self.mice.all(),
                key=lambda mouse: mouse.mouse_id.lower(),
            )
        ]

        if not mouse_ids:
            return "No mice"

        return ", ".join(mouse_ids)

    def can_be_modified_by(self, user):
        return bool(
            user
            and user.is_authenticated
            and (user.is_staff or self.owner_id == user.id)
        )

    def clean(self):
        super().clean()

        if not isinstance(self.data_paths, list):
            raise ValidationError(
                {"data_paths": "Recording paths must be a list."}
            )

        if any(
            not isinstance(path, str) or not path.strip()
            for path in self.data_paths
        ):
            raise ValidationError(
                {
                    "data_paths": (
                        "Recording paths must contain only non-empty strings."
                    ),
                }
            )

        if not isinstance(self.values, dict):
            raise ValidationError({"values": "Recording values must be an object."})

        if (
            self.experiment_id
            and self.recording_type_id
            and self.experiment.recording_type_id
            and self.experiment.recording_type_id != self.recording_type_id
        ):
            raise ValidationError(
                {
                    "recording_type": (
                        "Recording type must match the experiment type."
                    ),
                }
            )

        if not self.recording_type_id:
            return

        fields = list(self.recording_type.fields.all())
        field_by_key = {
            field.key: field
            for field in fields
        }
        unknown_keys = sorted(set(self.values) - set(field_by_key))

        if unknown_keys:
            raise ValidationError(
                {
                    "values": (
                        "Unknown recording field"
                        f"{'s' if len(unknown_keys) != 1 else ''}: "
                        f"{', '.join(unknown_keys)}."
                    ),
                }
            )

        errors = []
        for field in fields:
            value = self.values.get(field.key)

            try:
                validate_recording_field_value(
                    field.data_type,
                    value,
                    choices=field.choices,
                    required=field.required,
                    label=field.label,
                )
            except ValidationError as exc:
                errors.extend(exc.messages)

        if errors:
            raise ValidationError({"values": errors})


class ImageStorageLocation(models.Model):
    directory = models.CharField(max_length=1024, unique=True)

    def __str__(self):
        return self.directory


class ImageStorageSettings(models.Model):
    # A single database-backed setting is shared by every application worker.
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    active_location = models.ForeignKey(ImageStorageLocation, on_delete=models.PROTECT)


class RecordingImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name="images")
    storage_location = models.ForeignKey(ImageStorageLocation, on_delete=models.PROTECT)
    filename = models.CharField(max_length=255)
    original_name = models.CharField(max_length=255)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size_bytes = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["storage_location", "filename"], name="recording_image_file_idx")]


class SpreadsheetImportImage(models.Model):
    """Compressed shared images awaiting a reviewed spreadsheet import."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    draft = models.ForeignKey(SpreadsheetImportDraft, on_delete=models.CASCADE, related_name="shared_images")
    storage_location = models.ForeignKey(ImageStorageLocation, on_delete=models.PROTECT)
    filename = models.CharField(max_length=255)
    original_name = models.CharField(max_length=255)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size_bytes = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["storage_location", "filename"], name="import_image_file_idx")]


class SharedAnimalIdentity(models.Model):
    """Permanent sharing identity; a deleted Animal ID can never adopt its links."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mouse = models.OneToOneField(
        Mouse, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="sharing_identity",
    )


class SharedLink(models.Model):
    ANIMALS = "m"
    RECORDING = "r"
    kind = models.CharField(max_length=1, choices=[(ANIMALS, "Animals"), (RECORDING, "Recording")])
    code = models.CharField(max_length=64, unique=True)
    lookup_key = models.CharField(max_length=64, unique=True)
    selection_key = models.CharField(max_length=64, null=True, blank=True, unique=True)
    animals = models.ManyToManyField(SharedAnimalIdentity, blank=True)
    recording = models.OneToOneField(
        Recording, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="shared_link",
    )
    animal_tab = models.CharField(max_length=24, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)


class ShareCodeCounter(models.Model):
    """Allocation totals include retired codes, which must never be recycled."""
    kind = models.CharField(max_length=1, primary_key=True)
    allocated = models.PositiveBigIntegerField(default=0)
    use_three_words = models.BooleanField(default=False)
