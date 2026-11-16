"""Validated inputs for the optional FLARE AI assistant."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


SearchText = Annotated[str, Field(min_length=1, max_length=1000)]
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
    status: Literal["Alive", "Culled"] | None = None
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

    @model_validator(mode="after")
    def check_filter_combination(self) -> Self:
        values = self.model_dump(exclude_none=True, exclude_defaults=True)
        if not values:
            raise ValueError("Provide at least one search filter.")
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
        return self
