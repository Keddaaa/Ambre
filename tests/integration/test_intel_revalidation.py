"""Revalidating stored news links: traced, repeatable, and blind to the decision history."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ambre.config.settings import Settings
from ambre.infrastructure.db import make_engine, make_session_factory
from ambre.infrastructure.orm import Base, MarketRow
from ambre.intelligence import store
from ambre.intelligence.matching import CriteriaStatus, MarketLink
from ambre.intelligence.orm import IntelMarketLinkRow, IntelObservationRow
from ambre.intelligence.planning import load_gazetteer
from ambre.intelligence.resolution import load_rules
from ambre.intelligence.revalidation import (
    LINK_RULE_VERSION,
    invalidate_links,
    review_news_links,
)

T = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
QUESTION = "Will China invade Taiwan before 2027?"
KEPT = "Taiwan drills as China masses ships in the strait"
FALSE_LINK = "China to launch a new rocket this week"


def add_link(s: Session, n: int, title: str, relation: str = "news_mention") -> None:
    obs_id = f"synthetic_obs_{n}"
    s.add(
        IntelObservationRow(
            observation_id=obs_id,
            provider_id="synthetic_feed",
            domain="news",
            kind="headline",
            payload_type="headline",
            payload={},
            title=title,
            source_url=f"https://example.test/{n}",
            available_at=T,
            ingested_at=T,
            verification_status="unverified",
        )
    )
    s.flush()
    s.add(
        IntelMarketLinkRow(
            market_id="synthetic_m",
            observation_id=obs_id,
            relation=relation,
            criteria_status="news_mention_criteria_unread",
            evidence_level="unverified",
            usable_for_prediction=False,
            reasons=["original reason"],
            created_at=T,
        )
    )


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    made = make_session_factory(engine)
    with made() as s:
        s.add(
            MarketRow(
                id="synthetic_m",
                question=QUESTION,
                slug="synthetic-m",
                url="https://manifold.markets/synthetic/m",
                creator_id="synthetic",
                creator_username="synthetic",
                mechanism="cpmm-1",
                created_at=T - timedelta(days=30),
                close_at=T + timedelta(days=60),
                is_resolved=False,
                group_slugs=[],
                raw={},
                first_seen_at=T - timedelta(days=30),
                last_observed_at=T,
            )
        )
        s.flush()
        add_link(s, 1, KEPT)
        add_link(s, 2, FALSE_LINK)
        add_link(s, 3, FALSE_LINK, relation="earthquake_candidate")
        s.commit()
    yield made
    engine.dispose()


def review(s: Session) -> dict[str, bool]:
    settings = Settings()
    reviews = review_news_links(s, T, load_gazetteer(settings.gazetteer_path), load_rules())
    return {r.title or "": r.keep for r in reviews}


def apply(s: Session, at: datetime = T) -> int:
    settings = Settings()
    reviews = review_news_links(s, at, load_gazetteer(settings.gazetteer_path), load_rules())
    return invalidate_links(s, reviews, at)


def test_a_review_changes_nothing(factory: sessionmaker[Session]) -> None:
    with factory() as s:
        assert review(s) == {KEPT: True, FALSE_LINK: False}
        s.commit()
        assert not [r for r in s.scalars(select(IntelMarketLinkRow)) if r.invalidated_at]


def test_invalidation_keeps_the_row_and_says_why(factory: sessionmaker[Session]) -> None:
    with factory() as s:
        assert apply(s) == 1
        s.commit()
        row = s.scalars(
            select(IntelMarketLinkRow).where(IntelMarketLinkRow.observation_id == "synthetic_obs_2")
        ).one()
        assert row.invalidated_at == T
        assert row.invalidated_by == LINK_RULE_VERSION
        assert row.invalidated_reason and "topic" in row.invalidated_reason
        assert row.reasons == ["original reason"]  # the original record is not rewritten
        assert row.created_at == T


def test_only_news_links_are_reviewed(factory: sessionmaker[Session]) -> None:
    with factory() as s:
        apply(s)
        s.commit()
        other = s.scalars(
            select(IntelMarketLinkRow).where(IntelMarketLinkRow.observation_id == "synthetic_obs_3")
        ).one()
        assert other.invalidated_at is None


def test_a_second_pass_finds_nothing_and_keeps_the_first_trace(
    factory: sessionmaker[Session],
) -> None:
    later = T + timedelta(days=1)
    with factory() as s:
        apply(s)
        s.commit()
        assert apply(s, later) == 0
        s.commit()
        row = s.scalars(
            select(IntelMarketLinkRow).where(IntelMarketLinkRow.observation_id == "synthetic_obs_2")
        ).one()
        assert row.invalidated_at == T


def test_a_later_ingest_cannot_bring_an_invalidated_link_back(
    factory: sessionmaker[Session],
) -> None:
    link = MarketLink(
        market_id="synthetic_m",
        observation_id="synthetic_obs_2",
        relation="news_mention",
        criteria_status=CriteriaStatus.NEWS_MENTION_CRITERIA_UNREAD,
        evidence_level="unverified",
        usable_for_prediction=False,
        reasons=(),
    )
    with factory() as s:
        apply(s)
        assert store.save_link(s, link, T) is False
        s.commit()
        rows = s.scalars(
            select(IntelMarketLinkRow).where(IntelMarketLinkRow.observation_id == "synthetic_obs_2")
        ).all()
        assert len(rows) == 1 and rows[0].invalidated_at == T


def test_an_invalidated_link_no_longer_feeds_the_market(factory: sessionmaker[Session]) -> None:
    with factory() as s:
        before = {o.observation_id for o in _observations(s)}
        apply(s)
        s.commit()
        after = {o.observation_id for o in _observations(s)}
    assert before - after == {"synthetic_obs_2"}


def _observations(s: Session) -> list[IntelObservationRow]:
    return list(
        s.scalars(
            select(IntelObservationRow).where(
                IntelObservationRow.observation_id.in_(
                    store.active_observation_ids(s, "synthetic_m")
                )
            )
        )
    )
