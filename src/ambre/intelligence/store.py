"""Persistence helpers of the Global Intelligence Network. The caller owns the transaction."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ambre.intelligence.context import FeatureSet
from ambre.intelligence.feature_journal import fingerprint, journal_payload
from ambre.intelligence.generic_planning import MarketPlan
from ambre.intelligence.matching import MarketLink
from ambre.intelligence.models import Observation
from ambre.intelligence.orm import (
    IntelFeatureJournalRow,
    IntelMarketLinkRow,
    IntelObservationRow,
    IntelProviderStateRow,
    IntelRequestRow,
    IntelUsageRow,
)


def usage_since(session: Session, provider_id: str, since: datetime) -> list[datetime]:
    return list(
        session.scalars(
            select(IntelUsageRow.at)
            .where(IntelUsageRow.provider_id == provider_id, IntelUsageRow.at > since)
            .order_by(IntelUsageRow.at)
        )
    )


def record_usage(
    session: Session,
    provider_id: str,
    at: datetime,
    *,
    purpose: str,
    request_id: str | None,
    units: int = 1,
) -> None:
    session.add(
        IntelUsageRow(
            provider_id=provider_id, at=at, units=units, purpose=purpose, request_id=request_id
        )
    )


def get_state(session: Session, provider_id: str) -> IntelProviderStateRow | None:
    return session.get(IntelProviderStateRow, provider_id)


def mark_success(session: Session, provider_id: str, now: datetime) -> None:
    row = get_state(session, provider_id)
    if row is None:
        row = IntelProviderStateRow(provider_id=provider_id, consecutive_failures=0, updated_at=now)
        session.add(row)
    row.consecutive_failures = 0
    row.paused_until = None
    row.last_ok_at = now
    row.last_error = None
    row.updated_at = now


def mark_failure(
    session: Session, provider_id: str, now: datetime, error: str, pause_seconds: float
) -> IntelProviderStateRow:
    row = get_state(session, provider_id)
    if row is None:
        row = IntelProviderStateRow(provider_id=provider_id, consecutive_failures=0, updated_at=now)
        session.add(row)
    row.consecutive_failures += 1
    row.last_error = error
    row.paused_until = datetime.fromtimestamp(now.timestamp() + pause_seconds, now.tzinfo)
    row.updated_at = now
    return row


def last_request(
    session: Session,
    need: str,
    provider_id: str,
    statuses: tuple[str, ...],
    *,
    group_key: str | None = None,
) -> IntelRequestRow | None:
    """The newest request of this need and provider with one of ``statuses``. ``group_key``
    keeps answers about different places from standing in for one another."""
    rows = session.scalars(
        select(IntelRequestRow)
        .where(
            IntelRequestRow.need == need,
            IntelRequestRow.provider_id == provider_id,
            IntelRequestRow.status.in_(statuses),
        )
        .order_by(IntelRequestRow.created_at.desc())
        .limit(200)
    )
    for row in rows:
        if group_key is None or row.params.get("group_key") == group_key:
            return row
    return None


def save_observation(session: Session, obs: Observation, request_id: str) -> bool:
    """Insert once. A known id is the same record at the same revision: nothing is rewritten."""
    if session.get(IntelObservationRow, obs.observation_id) is not None:
        return False
    session.add(
        IntelObservationRow(
            observation_id=obs.observation_id,
            provider_id=obs.provider_id,
            domain=obs.domain.value,
            kind=obs.kind.value,
            payload_type=obs.payload.payload_type,
            payload=obs.payload.model_dump(mode="json"),
            title=obs.title,
            source_url=obs.source_url,
            event_at=obs.event_at,
            published_at=obs.published_at,
            available_at=obs.available_at,
            ingested_at=obs.ingested_at,
            verification_status=obs.verification_status.value,
            source_reliability=obs.source_reliability,
            revision=obs.revision,
            request_id=request_id,
        )
    )
    return True


def save_link(session: Session, link: MarketLink, now: datetime) -> bool:
    exists = session.scalar(
        select(IntelMarketLinkRow.id).where(
            IntelMarketLinkRow.market_id == link.market_id,
            IntelMarketLinkRow.observation_id == link.observation_id,
        )
    )
    if exists is not None:
        return False
    session.add(
        IntelMarketLinkRow(
            market_id=link.market_id,
            observation_id=link.observation_id,
            relation=link.relation,
            criteria_status=link.criteria_status.value,
            evidence_level=link.evidence_level,
            usable_for_prediction=link.usable_for_prediction,
            reasons=list(link.reasons),
            created_at=now,
        )
    )
    return True


def observation_from_row(row: IntelObservationRow) -> Observation:
    return Observation.model_validate(
        {
            "observation_id": row.observation_id,
            "provider_id": row.provider_id,
            "domain": row.domain,
            "kind": row.kind,
            "payload": row.payload,
            "ingested_at": row.ingested_at,
            "verification_status": row.verification_status,
            "title": row.title,
            "source_url": row.source_url,
            "event_at": row.event_at,
            "published_at": row.published_at,
            "available_at": row.available_at,
            "source_reliability": row.source_reliability,
            "revision": row.revision,
        }
    )


def market_observations(session: Session, market_id: str) -> list[Observation]:
    """Every stored observation linked to a market, oldest first (the bridge applies the
    availability rules; nothing is filtered here)."""
    rows = session.scalars(
        select(IntelObservationRow)
        .join(
            IntelMarketLinkRow,
            IntelMarketLinkRow.observation_id == IntelObservationRow.observation_id,
        )
        .where(IntelMarketLinkRow.market_id == market_id)
        .order_by(IntelObservationRow.ingested_at, IntelObservationRow.observation_id)
    )
    return [observation_from_row(r) for r in rows]


def append_feature_journal(
    session: Session,
    plan: MarketPlan,
    features: FeatureSet,
    now: datetime,
    market_probability: float | None,
) -> bool:
    """Journal what is known about a market now, unless that is empty or already on file.

    Returns True when a row was written. The newest row of the market is compared by
    fingerprint, so a pass that learned nothing new adds nothing.
    """
    payload = journal_payload(features)
    if not payload:
        return False
    digest = fingerprint(payload)
    latest = session.scalar(
        select(IntelFeatureJournalRow.fingerprint)
        .where(IntelFeatureJournalRow.market_id == plan.market_id)
        .order_by(IntelFeatureJournalRow.as_of.desc(), IntelFeatureJournalRow.id.desc())
        .limit(1)
    )
    if latest == digest:
        return False
    session.add(
        IntelFeatureJournalRow(
            market_id=plan.market_id,
            as_of=now,
            domain=plan.domain.value,
            need=plan.need.value if plan.need is not None else None,
            level=plan.level,
            estimable=plan.estimable,
            market_probability=market_probability,
            fingerprint=digest,
            features=payload,
        )
    )
    return True
