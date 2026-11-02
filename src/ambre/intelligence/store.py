"""Persistence helpers of the Global Intelligence Network. The caller owns the transaction."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ambre.intelligence.matching import MarketLink
from ambre.intelligence.models import Observation
from ambre.intelligence.orm import (
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
    session: Session, need: str, provider_id: str, statuses: tuple[str, ...]
) -> IntelRequestRow | None:
    return session.scalars(
        select(IntelRequestRow)
        .where(
            IntelRequestRow.need == need,
            IntelRequestRow.provider_id == provider_id,
            IntelRequestRow.status.in_(statuses),
        )
        .order_by(IntelRequestRow.created_at.desc())
        .limit(1)
    ).first()


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
