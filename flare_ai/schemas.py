"""Validated inputs for the optional FLARE AI assistant."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


SearchText = Annotated[str, Field(min_length=1, max_length=1000)]
ShortText = Annotated[str, Field(max_length=500)]
MissingMouseField = Literal[
    "date_of_birth", "date_added", "protocol_start_date", "cull_date",
    "added_by", "mother", "father", "mother_external_id", "father_external_id",
    "crossing_definition", "tattoo", "colour", "sex", "severity", "notes",
    "breeding_pair", "genotype", "crossing", "crossing_type", "custom_metadata",
]


class MouseGenotypeFilter(BaseModel):
    """Conditions in one entry must match the SAME recorded genotype call."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    line: SearchText | None = Field(
        default=None, description="Partial line name or alias, ignoring case."
    )
    category: SearchText | None = Field(
        default=None, description="Exact configured genotype code or label, ignoring case."
    )
    uncertain: bool | None = Field(default=None, description="Whether the call is marked uncertain.")

    @model_validator(mode="after")
    def require_condition(self) -> Self:
        if all(value is None for value in (self.line, self.category, self.uncertain)):
            raise ValueError("Provide a genotype line, category or uncertainty flag.")
        return self


class AnimalDraftRow(BaseModel):
    """Values that can differ between animals in one prepared litter."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    number: Annotated[str, Field(max_length=100)] = ""
    local_identifier: Annotated[str, Field(max_length=100)] = ""
    sex: Literal["M", "F", "unknown"] | None = None
    genotypes: dict[ShortText, ShortText] = Field(default_factory=dict, max_length=20)
    uncertain_genotypes: list[ShortText] = Field(default_factory=list, max_length=20)


class AnimalFormDraftInput(BaseModel):
    """A review-only draft for the existing Add animals form.

    Use either protocol_id or protocol_number (plus licence_reference when the
    number is ambiguous). Use crossing_id after the tool returns candidates, or
    crossing for an exact/partial catalogue search. For a single arbitrary
    Animal ID, put the complete ID in mouse_id_prefix and leave mouse_numbers
    blank. For a litter, use a shared prefix and the form's number/range syntax.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    protocol_id: int | None = Field(default=None, gt=0)
    protocol_number: int | None = None
    licence_reference: ShortText | None = None
    protocol_query: ShortText | None = None
    crossing_id: int | None = Field(default=None, gt=0)
    crossing: ShortText | None = None

    generate_ids: bool = False
    mouse_id_prefix: Annotated[str, Field(max_length=100)] = ""
    mouse_numbers: Annotated[str, Field(max_length=5000)] = ""
    date_of_birth: date | None = None
    breeding_pair: Annotated[str, Field(max_length=100)] = ""
    colour: Annotated[str, Field(max_length=100)] = ""
    status: Literal["Alive", "Deceased"] = "Alive"
    severity: Literal["Non-recovery", "Mild", "Moderate", "Severe"] | None = None
    protocol_start_date: date | None = None
    cull_date: date | None = None
    notes: Annotated[str, Field(max_length=4000)] = ""

    shared_genotypes: dict[ShortText, ShortText] = Field(default_factory=dict, max_length=20)
    shared_uncertain_genotypes: list[ShortText] = Field(default_factory=list, max_length=20)
    animals: list[AnimalDraftRow] = Field(default_factory=list, max_length=100)

    mother_id: Annotated[str, Field(max_length=255)] | None = None
    father_id: Annotated[str, Field(max_length=255)] | None = None
    keep_unlinked_mother: bool = False
    keep_unlinked_father: bool = False


class MouseSearchInput(BaseModel):
    """Read-only mouse filters combined with AND; omit unwanted conditions.

    Text searches are case-insensitive substrings unless described as exact.
    Dates and date bounds are inclusive. No arbitrary ORM lookups or SQL.
    """

    # Reject invented fields and trim whitespace around names.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mouse_id: SearchText | None = Field(default=None, description="Exact Animal ID, ignoring case.")
    mouse_id_contains: SearchText | None = Field(default=None, description="Part of an Animal ID.")
    mouse_ids: list[SearchText] | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        description="Exact Animal IDs to find together.",
        )
    id_status: Literal["temporary", "assigned", "unknown"] | None = None
    local_identifier: SearchText | None = Field(
        default=None, description="Exact local marking (model field tattoo), ignoring case; not a unique Animal ID."
    )
    marking_type: Literal["other", "tattoo", "ear_clip"] | None = None
    colour: SearchText | None = None
    sex: Literal["M", "F", "unknown"] | None = None
    status: Literal["Alive", "Deceased"] | None = None
    severity: Literal["Non-recovery", "Mild", "Moderate", "Severe", "unknown"] | None = None
    severity_exceeded: bool | None = Field(
        default=None, description="Whether animal severity exceeds its protocol maximum."
    )
    breeding_pair: SearchText | None = None
    notes: SearchText | None = Field(default=None, description="Literal text in animal notes; not semantic search.")

    date_of_birth: date | None = Field(default=None, description="Exact birth date.")
    born_from: date | None = Field(default=None, description="Earliest birth date, inclusive.")
    born_to: date | None = Field(default=None, description="Latest birth date, inclusive.")
    date_added: date | None = Field(default=None, description="Exact date the animal was added to the ELN.")
    added_from: date | None = None
    added_to: date | None = None
    protocol_start_date: date | None = None
    protocol_start_from: date | None = None
    protocol_start_to: date | None = None
    cull_date: date | None = None
    culled_from: date | None = None
    culled_to: date | None = None

    crossing: SearchText | None = Field(
        default=None, description="Partial crossing name, constituent line name/alias, or legacy crossing text."
    )
    crossing_id: int | None = Field(default=None, gt=0, description="Exact crossing catalogue ID.")
    crossing_type: Literal["WT", "Single", "Multiple"] | None = Field(
        default=None, description="Crossing type inferred from current lines, or legacy type when no crossing is linked."
    )
    species: SearchText | None = Field(default=None, description="Species name from the linked crossing.")
    genotype: SearchText | None = Field(
        default=None, description="Literal text in the saved genotype summary, including legacy records. Use genotypes for precise per-line calls."
    )
    genotypes: list[MouseGenotypeFilter] = Field(
        default_factory=list, max_length=10,
        description="Every entry must match a recorded call; within each entry, line/category/uncertainty refer to the same call."
    )
    sort_by: Literal[
        "date_added",
        "date_of_birth",
        "mouse_id",
    ] = "date_added"

    sort_direction: Literal["asc", "desc"] = "desc"

    protocol: SearchText | None = Field(default=None, description="Partial protocol name.")
    protocol_id: int | None = Field(default=None, gt=0, description="Exact database protocol ID; different from protocol number.")
    protocol_number: int | None = None
    licence_reference: SearchText | None = None
    added_by: SearchText | None = Field(default=None, description="Partial creator username, usually initials.")
    added_by_id: int | None = Field(default=None, gt=0)
    mother_id: SearchText | None = Field(default=None, description="Exact linked mother's Animal ID, ignoring case.")
    father_id: SearchText | None = Field(default=None, description="Exact linked father's Animal ID, ignoring case.")
    mother_external_id: SearchText | None = Field(default=None, description="Exact originally recorded mother ID, ignoring case; may be unlinked.")
    father_external_id: SearchText | None = Field(default=None, description="Exact originally recorded father ID, ignoring case; may be unlinked.")

    custom_metadata: dict[str, JsonValue] | None = Field(
        default=None, min_length=1, max_length=20,
        description="Exact values for literal top-level metadata keys; all supplied entries must match. JSON null is different from a missing key."
    )
    missing: list[MissingMouseField] = Field(
        default_factory=list, max_length=20, description="Fields that must be absent (NULL, blank text, or empty metadata object)."
    )
    present: list[MissingMouseField] = Field(
        default_factory=list, max_length=20, description="Fields that must be populated."
    )

    # Field types validate individual values; this checks relationships between them.
    @model_validator(mode="after")
    def check_filter_combination(self) -> Self:
        filter_values = self.model_dump(
            exclude={"sort_by", "sort_direction"},
            exclude_none=True,
            exclude_defaults=True,
        )

        if not filter_values:
            raise ValueError("Provide at least one search filter.")

        for exact_name, lower_name, upper_name in (
            ("date_of_birth", "born_from", "born_to"),
            ("date_added", "added_from", "added_to"),
            ("protocol_start_date", "protocol_start_from", "protocol_start_to"),
            ("cull_date", "culled_from", "culled_to"),
        ):
            exact, lower, upper = (getattr(self, name) for name in (exact_name, lower_name, upper_name))
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f"{lower_name} must be on or before {upper_name}.")
            if exact is not None and ((lower is not None and exact < lower) or (upper is not None and exact > upper)):
                raise ValueError(f"{exact_name} must be within its requested range.")
        if set(self.missing) & set(self.present):
            raise ValueError("A field cannot be both missing and present.")
        if self.custom_metadata and any(not key.strip() or len(key) > 200 for key in self.custom_metadata):
            raise ValueError("Metadata keys must contain 1–200 characters.")
        return self


class ProjectSearchInput(BaseModel):
    """Read-only project filters combined with AND; omit unwanted conditions.

    Text is matched as a case-insensitive substring. Dates and date bounds are
    inclusive. Lab-wide service projects are excluded unless is_service is true.
    """

    # Reject invented fields and trim whitespace around names.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: SearchText | None = Field(default=None, description="Partial project name, ignoring case.")
    lab_member: SearchText | None = Field(default=None, description="Partial lab member name.")
    description: SearchText | None = Field(default=None, description="Literal text in the project description.")
    active: bool | None = Field(default=None, description="Whether the project is active.")
    is_service: bool | None = Field(default=None, description="Set true to find the lab-wide service project.")
    start_date_from: date | None = Field(default=None, description="Earliest start date, inclusive.")
    start_date_to: date | None = Field(default=None, description="Latest start date, inclusive.")
    end_date_from: date | None = Field(default=None, description="Earliest end date, inclusive.")
    end_date_to: date | None = Field(default=None, description="Latest end date, inclusive.")


class ExperimentSearchInput(BaseModel):
    """Read-only experiment filters combined with AND; omit unwanted conditions.

    Text is matched as a case-insensitive substring. Dates and date bounds are
    inclusive. Use project for a partial project name, or project_id to pin one
    project returned by search_projects. Lab-wide service experiments are
    excluded unless is_service is true.
    """

    # Reject invented fields and trim whitespace around names.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: SearchText | None = Field(default=None, description="Partial experiment name, ignoring case.")
    description: SearchText | None = Field(default=None, description="Literal text in the experiment description.")
    project: SearchText | None = Field(default=None, description="Partial name of the parent project.")
    project_id: int | None = Field(default=None, gt=0, description="Exact project ID (from search_projects).")
    recording_type: SearchText | None = Field(default=None, description="Partial experiment type name.")
    archived: bool | None = Field(default=None, description="Whether the experiment is archived.")
    is_service: bool | None = Field(default=None, description="Set true to find lab-wide service experiments.")
    start_date_from: date | None = Field(default=None, description="Earliest start date, inclusive.")
    start_date_to: date | None = Field(default=None, description="Latest start date, inclusive.")


class RecordingSearchInput(BaseModel):
    """Read-only recording filters combined with AND."""

    # Reject invented fields and trim whitespace around names.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    owner: SearchText | None = Field(
        default=None, description="Partial username of the recording owner."
    )
    experiment_contains: SearchText | None = Field(
        default=None, description="Partial experiment name, ignoring case."
    )

    mouse_id: SearchText | None = Field(default=None, description="Exact Animal ID, ignoring case.")
    mouse_id_contains: SearchText | None = Field(default=None, description="Part of an Animal ID.")
    animal_age_days: int | None = Field(
        default=None,
        ge=0,
        description="Exact animal age in postnatal days on the recording date.",
    )
    animal_age_days_from: int | None = Field(
        default=None,
        ge=0,
        description="Minimum animal age in postnatal days on the recording date, inclusive.",
    )
    animal_age_days_to: int | None = Field(
        default=None,
        ge=0,
        description="Maximum animal age in postnatal days on the recording date, inclusive.",
    )

    # --- multi-mouse presence (independent of the same-mouse property block) ---
    mouse_ids: list[SearchText] | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        description="Exact Animal IDs that must ALL be present in the recording. Independent of the same-mouse property filters below.",
    )

    # --- same-mouse property filters (all refer to one linked mouse) ---
    crossing: SearchText | None = Field(
        default=None,
        description="Partial crossing name, constituent line name/alias, or legacy crossing text.",
    )
    crossing_type: Literal["WT", "Single", "Multiple"] | None = Field(
        default=None,
        description="Crossing type (WT, Single Mutation, or Multiple).",
    )
    sex: Literal["M", "F", "unknown"] | None = Field(
        default=None, description="Mouse sex."
    )
    colour: SearchText | None = Field(
        default=None, description="Mouse coat colour."
    )
    genotype: SearchText | None = Field(
        default=None,
        description="Partial genotype: legacy genotype text, a structured genotype line name, or zygosity (e.g. 'Gad2' or 'Het').",
    )
    species: SearchText | None = Field(
        default=None, description="Species name from the linked crossing."
    )
    breeding_pair: SearchText | None = Field(
        default=None, description="Breeding pair identifier."
    )
    status: Literal["Alive", "Deceased"] | None = Field(
        default=None, description="Mouse status."
    )
    severity: Literal["Non-recovery", "Mild", "Moderate", "Severe", "unknown"] | None = Field(
        default=None, description="Severity level."
    )

    recording_type: SearchText | None = Field(
        default=None, description="Partial recording type name, such as ephys or behaviour."
    )
    recording_date: date | None = Field(default=None, description="Exact recording date.")
    recording_date_from: date | None = Field(
        default=None, description="Earliest recording date, inclusive."
    )
    recording_date_to: date | None = Field(
        default=None, description="Latest recording date, inclusive."
    )
    notes_contains: SearchText | None = Field(
        default=None, description="Literal text contained in recording notes."
    )
    project_contains: SearchText | None = Field(
        default=None, description="Partial project name, ignoring case."
    )
    project_id: int | None = Field(
        default=None, gt=0, description="Exact project ID (from search_projects) to pin results to one project."
    )
    experiment_id: int | None = Field(
        default=None, gt=0, description="Exact experiment ID (from search_experiments) to pin results to one experiment."
    )

    @model_validator(mode="after")
    def check_filter_combination(self) -> Self:
        values = self.model_dump(exclude_none=True, exclude_defaults=True)
        if not values:
            raise ValueError("Provide at least one search filter.")
        if self.mouse_id is not None and self.mouse_ids is not None:
            raise ValueError("Use mouse_id OR mouse_ids, not both.")
        if (
            self.recording_date_from is not None
            and self.recording_date_to is not None
            and self.recording_date_from > self.recording_date_to
        ):
            raise ValueError("recording_date_from must be on or before recording_date_to.")
        if self.recording_date is not None and (
            (
                self.recording_date_from is not None
                and self.recording_date < self.recording_date_from
            )
            or (
                self.recording_date_to is not None
                and self.recording_date > self.recording_date_to
            )
        ):
            raise ValueError("recording_date must be within the requested range.")
        if (
            self.animal_age_days_from is not None
            and self.animal_age_days_to is not None
            and self.animal_age_days_from > self.animal_age_days_to
        ):
            raise ValueError(
                "animal_age_days_from must be on or before animal_age_days_to."
            )
        if self.animal_age_days is not None and (
            (
                self.animal_age_days_from is not None
                and self.animal_age_days < self.animal_age_days_from
            )
            or (
                self.animal_age_days_to is not None
                and self.animal_age_days > self.animal_age_days_to
            )
        ):
            raise ValueError("animal_age_days must be within its requested range.")
        return self



class ProcedureSearchInput(BaseModel):
    """Read-only procedure filters combined with AND."""

    # Reject invented fields and trim whitespace around names.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mouse_id: SearchText | None = Field(default=None, description="Exact Animal ID, ignoring case.")
    mouse_id_contains: SearchText | None = Field(default=None, description="Part of an Animal ID.")
    animal_age_days: int | None = Field(
        default=None,
        ge=0,
        description="Exact animal age in postnatal days on the recording date.",
    )
    animal_age_days_from: int | None = Field(
        default=None,
        ge=0,
        description="Minimum animal age in postnatal days on the recording date, inclusive.",
    )
    animal_age_days_to: int | None = Field(
        default=None,
        ge=0,
        description="Maximum animal age in postnatal days on the recording date, inclusive.",
    )
    
    
    procedure_type: SearchText | None = Field(
        default=None, description="Partial procedure type name, ignoring case."
    )
    procedure_date: date | None = Field(default=None, description="Exact procedure date.")
    procedure_date_from: date | None = Field(
        default=None, description="Earliest procedure date, inclusive."
    )
    procedure_date_to: date | None = Field(
        default=None, description="Latest procedure date, inclusive."
    )

    notes_contains: SearchText | None = Field(
        default=None, description="Literal text contained in procedure notes."
    )
    lab_member: SearchText | None = Field(
        default=None, description="Partial username of the lab member who performed the procedure."
    )

    @model_validator(mode="after")
    def check_filter_combination(self) -> Self:
        values = self.model_dump(exclude_none=True, exclude_defaults=True)
        if not values:
            raise ValueError("Provide at least one search filter.")
        if (
            self.procedure_date_from is not None
            and self.procedure_date_to is not None
            and self.procedure_date_from > self.procedure_date_to
        ):
            raise ValueError("procedure_date_from must be on or before procedure_date_to.")
        if self.procedure_date is not None and (
            (
                self.procedure_date_from is not None
                and self.procedure_date < self.procedure_date_from
            )
            or (
                self.procedure_date_to is not None
                and self.procedure_date > self.procedure_date_to
            )
        ):
            raise ValueError("procedure_date must be within the requested range.")
        return self