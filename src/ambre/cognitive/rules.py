"""Rule registry: proposals, lifecycle, versions, conflicts and audit.

A proposal is checked structurally and parked as PROPOSED: it is inert data. A rule only changes
behaviour while ACTIVE, and reaching ACTIVE takes TESTING, then VALIDATED on measured held-out
evidence, then an owner activation that re-checks the rule against the limits in force today.
Every state is kept as a version row; nothing is rewritten or deleted.

Until the experiment engine exists (a later phase), the evidence behind a validation is supplied
by the caller and cannot be verified, so only the owner (``USER``) may validate or activate.
AMBRE and Claude can propose, test and reject, never promote.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from ambre.cognitive import vault
from ambre.cognitive.constraints import CurrentLimits, rules_conflict, validate_definition
from ambre.cognitive.lifecycle import check_transition, is_operative
from ambre.cognitive.models import ID_PATTERN, RuleDefinition, RuleEvidence, RuleStatus
from ambre.cognitive.orm import CognitiveNoteRow, RuleProposalRow, RuleVersionRow
from ambre.domain.models import Actor

#: Who may move a rule to VALIDATED or ACTIVE while evidence cannot be verified automatically.
PROMOTERS = frozenset({Actor.USER})
_PROMOTION_TARGETS = frozenset({RuleStatus.VALIDATED, RuleStatus.ACTIVE})
_CONFLICT_SCOPE = (
    RuleStatus.PROPOSED,
    RuleStatus.TESTING,
    RuleStatus.VALIDATED,
    RuleStatus.ACTIVE,
)


class RuleError(vault.VaultError):
    pass


class RuleNotFound(RuleError):
    pass


class RuleExists(RuleError):
    pass


class RuleVersionConflict(RuleError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"expected rule version {expected}, found {actual}")
        self.expected = expected
        self.actual = actual


class TransitionRefused(RuleError):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class RuleRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str
    name: str
    domain: str
    status: RuleStatus
    version: int
    definition: RuleDefinition
    evidence: RuleEvidence | None
    created_by: Actor
    source_note_ids: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


class RuleVersionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str
    version: int
    status: RuleStatus
    definition: RuleDefinition
    evidence: RuleEvidence | None
    changed_at: datetime
    changed_by: Actor
    reason: str | None


@dataclass(frozen=True)
class RuleConflict:
    rule_id: str
    status: RuleStatus
    reason: str


@dataclass
class ProposalOutcome:
    rule: RuleRecord
    accepted: bool
    reasons: list[str] = field(default_factory=list)
    conflicts: list[RuleConflict] = field(default_factory=list)


def is_active(rule: RuleRecord) -> bool:
    return is_operative(rule.status)


def _to_record(row: RuleProposalRow) -> RuleRecord:
    return RuleRecord(
        rule_id=row.id,
        name=row.name,
        domain=row.domain,
        status=RuleStatus(row.status),
        version=row.version,
        definition=RuleDefinition.model_validate(row.definition),
        evidence=RuleEvidence.model_validate(row.evidence) if row.evidence is not None else None,
        created_by=Actor(row.created_by),
        source_note_ids=tuple(row.source_note_ids),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _version_row(
    rule_id: str,
    version: int,
    status: RuleStatus,
    definition: RuleDefinition,
    evidence: RuleEvidence | None,
    *,
    at: datetime,
    by: Actor,
    reason: str | None,
) -> RuleVersionRow:
    return RuleVersionRow(
        rule_id=rule_id,
        version=version,
        status=status.value,
        definition=definition.model_dump(mode="json"),
        evidence=evidence.model_dump(mode="json") if evidence is not None else None,
        changed_at=at,
        changed_by=by.value,
        reason=reason,
    )


def get_rule(session: Session, rule_id: str) -> RuleRecord | None:
    row = session.get(RuleProposalRow, rule_id)
    return _to_record(row) if row is not None else None


def list_rules(session: Session, status: RuleStatus | None = None) -> list[RuleRecord]:
    stmt = select(RuleProposalRow).order_by(RuleProposalRow.created_at, RuleProposalRow.id)
    if status is not None:
        stmt = stmt.where(RuleProposalRow.status == status.value)
    return [_to_record(r) for r in session.scalars(stmt)]


def active_rules(session: Session) -> list[RuleRecord]:
    """The only rules allowed to influence behaviour."""
    return list_rules(session, RuleStatus.ACTIVE)


def rule_history(session: Session, rule_id: str) -> list[RuleVersionRecord]:
    rows = session.scalars(
        select(RuleVersionRow)
        .where(RuleVersionRow.rule_id == rule_id)
        .order_by(RuleVersionRow.version)
    )
    return [
        RuleVersionRecord(
            rule_id=r.rule_id,
            version=r.version,
            status=RuleStatus(r.status),
            definition=RuleDefinition.model_validate(r.definition),
            evidence=RuleEvidence.model_validate(r.evidence) if r.evidence is not None else None,
            changed_at=r.changed_at,
            changed_by=Actor(r.changed_by),
            reason=r.reason,
        )
        for r in rows
    ]


def find_conflicts(
    session: Session,
    definition: RuleDefinition,
    *,
    statuses: tuple[RuleStatus, ...],
    exclude: str | None = None,
) -> list[RuleConflict]:
    found: list[RuleConflict] = []
    rows = session.scalars(
        select(RuleProposalRow).where(
            RuleProposalRow.domain == definition.domain,
            RuleProposalRow.status.in_([s.value for s in statuses]),
        )
    )
    for row in rows:
        if row.id == exclude:
            continue
        why = rules_conflict(definition, RuleDefinition.model_validate(row.definition))
        if why is not None:
            found.append(RuleConflict(row.id, RuleStatus(row.status), why))
    return sorted(found, key=lambda c: c.rule_id)


def propose_rule(
    session: Session,
    definition: RuleDefinition,
    limits: CurrentLimits,
    *,
    source_note_ids: tuple[str, ...],
    actor: Actor,
    now: datetime,
    rule_id: str | None = None,
) -> ProposalOutcome:
    """Register a rule proposal. Never active; refused proposals are kept as REJECTED."""
    rid = rule_id or f"rule_{uuid.uuid4().hex[:10]}"
    if not ID_PATTERN.match(rid):
        raise ValueError(f"invalid rule id {rid!r}")
    if session.get(RuleProposalRow, rid) is not None:
        raise RuleExists(rid)

    reasons = validate_definition(definition, limits)
    if not source_note_ids:
        reasons.append("a rule needs at least one source note (its origin must be traceable)")
    for note_id in source_note_ids:
        if session.get(CognitiveNoteRow, note_id) is None:
            reasons.append(f"unknown source note {note_id}")
    conflicts = [] if reasons else find_conflicts(session, definition, statuses=_CONFLICT_SCOPE)

    accepted = not reasons
    first_status = RuleStatus.DRAFT if accepted else RuleStatus.REJECTED
    with session.begin_nested():
        row = RuleProposalRow(
            id=rid,
            name=definition.name,
            domain=definition.domain,
            status=first_status.value,
            version=1,
            definition=definition.model_dump(mode="json"),
            evidence=None,
            created_by=actor.value,
            source_note_ids=list(source_note_ids),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        session.add(
            _version_row(
                rid,
                1,
                first_status,
                definition,
                None,
                at=now,
                by=actor,
                reason=("created from notes " + ", ".join(source_note_ids))
                if accepted
                else "; ".join(reasons),
            )
        )
        if accepted:
            row.status = RuleStatus.PROPOSED.value
            row.version = 2
            session.add(
                _version_row(
                    rid,
                    2,
                    RuleStatus.PROPOSED,
                    definition,
                    None,
                    at=now,
                    by=actor,
                    reason="structural validation passed; inactive until tested and validated",
                )
            )
        vault.record_event(
            session,
            "rule_proposed" if accepted else "rule_rejected",
            "rule",
            rid,
            actor,
            {
                "reasons": reasons,
                "conflicts": [c.rule_id for c in conflicts],
                "source_note_ids": list(source_note_ids),
                "action": definition.action.type.value,
            },
            now,
        )
        session.flush()
    return ProposalOutcome(_to_record(row), accepted, reasons, conflicts)


def transition_rule(
    session: Session,
    rule_id: str,
    target: RuleStatus,
    *,
    actor: Actor,
    reason: str,
    expected_version: int,
    now: datetime,
    limits: CurrentLimits,
    evidence: RuleEvidence | None = None,
) -> RuleRecord:
    """Move a rule one legal step. A refusal changes nothing, is audited and raises."""
    row = session.get(RuleProposalRow, rule_id)
    if row is None:
        raise RuleNotFound(rule_id)
    if row.version != expected_version:
        raise RuleVersionConflict(expected_version, row.version)
    current = _to_record(row)
    effective = evidence if evidence is not None else current.evidence

    reasons = check_transition(current.status, target, effective)
    if target in _PROMOTION_TARGETS and actor not in PROMOTERS:
        reasons.append(
            f"{actor.value} cannot promote a rule: its evidence is self-reported, "
            "only the owner may validate or activate"
        )
    if target is RuleStatus.ACTIVE:
        reasons += validate_definition(current.definition, limits)
        conflicts = find_conflicts(
            session, current.definition, statuses=(RuleStatus.ACTIVE,), exclude=rule_id
        )
        reasons += [f"conflicts with active rule {c.rule_id}: {c.reason}" for c in conflicts]
    if reasons:
        vault.record_event(
            session,
            "rule_transition_refused",
            "rule",
            rule_id,
            actor,
            {
                "from": current.status.value,
                "to": target.value,
                "reasons": reasons,
                "reason": reason,
            },
            now,
        )
        session.flush()
        raise TransitionRefused(reasons)

    new_version = expected_version + 1
    store_evidence = effective if target in _PROMOTION_TARGETS else current.evidence
    with session.begin_nested():
        result = cast(
            "CursorResult[Any]",
            session.execute(
                update(RuleProposalRow)
                .where(
                    and_(RuleProposalRow.id == rule_id, RuleProposalRow.version == expected_version)
                )
                .values(
                    status=target.value,
                    version=new_version,
                    evidence=store_evidence.model_dump(mode="json") if store_evidence else None,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount != 1:
            session.expire(row)
            fresh = session.get(RuleProposalRow, rule_id)
            raise RuleVersionConflict(expected_version, fresh.version if fresh else -1)
        session.add(
            _version_row(
                rule_id,
                new_version,
                target,
                current.definition,
                store_evidence,
                at=now,
                by=actor,
                reason=reason,
            )
        )
        details: dict[str, Any] = {
            "from": current.status.value,
            "to": target.value,
            "reason": reason,
        }
        vault.record_event(session, "rule_transition", "rule", rule_id, actor, details, now)
        session.flush()
    session.expire(row)
    out = get_rule(session, rule_id)
    assert out is not None
    return out
