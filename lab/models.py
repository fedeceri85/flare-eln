from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

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
ZYGOSITY_CHOICES = [
    ("unknown", "Unknown"),
    ("wt", "WT"),
    ("het", "Het"),
    ("hom", "Hom"),
]
ZYGOSITY_LABELS = dict(ZYGOSITY_CHOICES)


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
    mouse_id = models.CharField(max_length=100, primary_key=True)
    date_of_birth = models.DateField(null=True, blank=True)
    tattoo = models.CharField(max_length=100, blank=True)
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

    def __str__(self):
            return self.mouse_id

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

        genotype_calls = (
            self.genotype_calls
            .select_related("mouse_line")
            .order_by("mouse_line__name", "mouse_line_id")
        )

        return "; ".join(
            f"{call.mouse_line.name}: {call.get_zygosity_display()}"
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

    class Meta:
        ordering = ["crossing", "line__name"]
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
        choices=ZYGOSITY_CHOICES,
        default="unknown",
    )

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
        for line in sorted(lines, key=lambda line: line.name.lower())
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
                CrossingLine(crossing=crossing, line=line)
                for line in normalized_lines
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
    sequence_number = models.PositiveIntegerField(default=1)
    data_path = models.TextField()
    notes = models.TextField(blank=True)
    values = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-recording_date", "sequence_number", "recording_id"]

    def __str__(self):
        return (
            f"{self.mice_label} | "
            f"{self.recording_date} | "
            f"{self.experiment.name} | "
            f"#{self.sequence_number}"
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
