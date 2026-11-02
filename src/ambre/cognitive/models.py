"""Cognitive Vault domain models (pure, no I/O).

Nothing here invents a value: confidence and evidence default to ``None`` / ``NONE``, a
hypothesis cannot be born validated, a fact needs a source, and a factual relation needs
evidence, never just similarity.
"""

import hashlib
import math
import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from ambre.domain.models import Actor

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
RULE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,63}$")
MAX_TITLE = 200
MAX_CONTENT = 20000

Scalar = str | int | float | bool


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NoteType(StrEnum):
    OBSERVATION = "OBSERVATION"
    FACT = "FACT"
    CLAIM = "CLAIM"
    HYPOTHESIS = "HYPOTHESIS"
    EXPERIENCE = "EXPERIENCE"
    ERROR = "ERROR"
    LESSON = "LESSON"
    RULE = "RULE"
    SHORTCUT = "SHORTCUT"
    METHOD = "METHOD"
    STRATEGY = "STRATEGY"
    SOURCE_PROFILE = "SOURCE_PROFILE"
    MARKET_CONTEXT = "MARKET_CONTEXT"


class NoteStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    ARCHIVED = "archived"
    REJECTED = "rejected"


class EvidenceLevel(StrEnum):
    NONE = "none"
    ANECDOTAL = "anecdotal"
    OBSERVED = "observed"
    REPLICATED = "replicated"
    MEASURED = "measured"


class RelationType(StrEnum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    DERIVED_FROM = "DERIVED_FROM"
    RELATED_TO = "RELATED_TO"
    APPLIES_TO = "APPLIES_TO"
    INVALIDATES = "INVALIDATES"
    REPLACES = "REPLACES"
    TESTED_BY = "TESTED_BY"
    CAUSED_ERROR = "CAUSED_ERROR"
    IMPROVED = "IMPROVED"
    DUPLICATES = "DUPLICATES"


#: Relations that assert something about the world: they need cited evidence.
FACTUAL_RELATIONS = frozenset(
    {
        RelationType.SUPPORTS,
        RelationType.CONTRADICTS,
        RelationType.CAUSED_ERROR,
        RelationType.INVALIDATES,
        RelationType.IMPROVED,
    }
)
SIMILARITY_METHODS = frozenset({"embedding_similarity", "text_similarity", "vector_similarity"})


class EdgeStatus(StrEnum):
    ACTIVE = "active"
    RETRACTED = "retracted"


class Provenance(_Frozen):
    created_by: Actor
    source_refs: tuple[str, ...] = ()
    method: str | None = None
    note: str | None = None


def _check_scalar_dict(value: dict[str, Scalar]) -> dict[str, Scalar]:
    for key, item in value.items():
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"{key}: non-finite number")
        if not isinstance(item, Scalar):
            raise ValueError(f"{key}: only scalar values are allowed")
    return value


class NoteDraft(_Frozen):
    title: str
    type: NoteType
    content: str
    provenance: Provenance
    note_id: str | None = None
    status: NoteStatus = NoteStatus.DRAFT
    evidence_level: EvidenceLevel = EvidenceLevel.NONE
    context: dict[str, Scalar] = Field(default_factory=dict)
    confidence: float | None = None  # never invented: None until measured
    valid_from: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > MAX_TITLE:
            raise ValueError(f"title must be 1..{MAX_TITLE} characters")
        return v

    @field_validator("content")
    @classmethod
    def _content(cls, v: str) -> str:
        if len(v) > MAX_CONTENT:
            raise ValueError(f"content longer than {MAX_CONTENT} characters")
        return v

    @field_validator("note_id")
    @classmethod
    def _id(cls, v: str | None) -> str | None:
        if v is not None and not ID_PATTERN.match(v):
            raise ValueError("note_id must match [a-z0-9][a-z0-9_-]{2,63}")
        return v

    @field_validator("context")
    @classmethod
    def _context(cls, v: dict[str, Scalar]) -> dict[str, Scalar]:
        return _check_scalar_dict(v)

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, v: float | None) -> float | None:
        if v is not None and not (math.isfinite(v) and 0.0 <= v <= 1.0):
            raise ValueError("confidence must be None or a finite number in [0, 1]")
        return v

    @model_validator(mode="after")
    def _rules(self) -> "NoteDraft":
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until precedes valid_from")
        if (
            self.type in (NoteType.HYPOTHESIS, NoteType.CLAIM)
            and self.status is NoteStatus.VALIDATED
        ):
            raise ValueError(
                f"a {self.type.value} cannot be validated without a validation mechanism"
            )
        if self.type is NoteType.FACT and not self.provenance.source_refs:
            raise ValueError("a FACT needs at least one source reference")
        return self


class NoteRecord(_Frozen):
    """A stored note at one version (what the vault returns)."""

    note_id: str
    version: int
    title: str
    type: NoteType
    content: str
    status: NoteStatus
    evidence_level: EvidenceLevel
    context: dict[str, Scalar]
    confidence: float | None
    provenance: Provenance
    created_at: AwareDatetime
    updated_at: AwareDatetime
    valid_from: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None


class EdgeDraft(_Frozen):
    src: str
    dst: str
    type: RelationType
    provenance: Provenance

    @model_validator(mode="after")
    def _rules(self) -> "EdgeDraft":
        if self.src == self.dst:
            raise ValueError("a note cannot be related to itself")
        p = self.provenance
        if self.type in FACTUAL_RELATIONS:
            if not p.source_refs:
                raise ValueError(f"{self.type.value} needs at least one source reference")
            if p.method in SIMILARITY_METHODS:
                raise ValueError(f"{self.type.value} cannot rest on similarity alone")
        elif not p.source_refs and not (p.note and p.note.strip()):
            raise ValueError("a relation needs a source reference or a justification note")
        return self


# ---------------------------------------------------------------------------- rules


class RuleStatus(StrEnum):
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    TESTING = "TESTING"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


class ActionType(StrEnum):
    """The closed list of things a rule may ask for. Anything else is refused by the model."""

    PRIORITIZE_SOURCE = "prioritize_source"
    DEPRIORITIZE_SOURCE = "deprioritize_source"
    PREFER_OFFICIAL_SOURCES = "prefer_official_sources"
    REDUCE_API_CALLS = "reduce_api_calls"
    TIGHTEN_LIMIT = "tighten_limit"
    RAISE_CONFIDENCE_THRESHOLD = "raise_confidence_threshold"
    SUSPEND_STRATEGY = "suspend_strategy"
    ADD_OBSERVATION_PERIOD = "add_observation_period"


class RuleAction(_Frozen):
    type: ActionType
    params: dict[str, Scalar] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _params(cls, v: dict[str, Scalar]) -> dict[str, Scalar]:
        return _check_scalar_dict(v)


class RuleRestrictions(_Frozen):
    cannot_override_hard_limits: bool = True


class RuleDefinition(_Frozen):
    name: str
    domain: str = Field(min_length=1, max_length=64)
    conditions: dict[str, Scalar] = Field(default_factory=dict)
    action: RuleAction
    restrictions: RuleRestrictions = Field(default_factory=RuleRestrictions)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not RULE_NAME_PATTERN.match(v):
            raise ValueError("rule name must be an identifier (letters, digits, underscore)")
        return v

    @field_validator("conditions")
    @classmethod
    def _conditions(cls, v: dict[str, Scalar]) -> dict[str, Scalar]:
        return _check_scalar_dict(v)


class RuleEvidence(_Frozen):
    """What a validation measured. Every field stays ``None`` until it is measured."""

    sample_count: Annotated[int, Field(ge=0)] | None = None
    validation_score: float | None = None
    method: Literal["held_out_backtest", "paired_experiment"] | None = None
    held_out: bool | None = None
    detail: str | None = None

    @field_validator("validation_score")
    @classmethod
    def _score(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("validation_score must be finite")
        return v


def content_fingerprint(note_type: NoteType, title: str, content: str) -> str:
    """Hash of the normalised substance (case and spacing folded), to spot exact duplicates."""

    def norm(s: str) -> str:
        return " ".join(s.lower().split())

    payload = f"{note_type.value}\x1f{norm(title)}\x1f{norm(content)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
