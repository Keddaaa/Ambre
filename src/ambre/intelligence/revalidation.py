"""Revalidation of stored news links under the current matching rule.

A link found wrong is flagged with the date, the reason and the rule, and its row stays. Nothing
here touches a decision: what a decision cited at the time remains part of its history.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ambre.infrastructure.orm import MarketRow
from ambre.intelligence.orm import IntelMarketLinkRow, IntelObservationRow
from ambre.intelligence.planning import Gazetteer
from ambre.intelligence.queries import topic_for, topic_relevant
from ambre.intelligence.resolution import ResearchRules, analyze_market
from ambre.intelligence.store import active_link

#: Change it with the rule (``topic_relevant``): it tells which rule invalidated a link.
LINK_RULE_VERSION = "topic-relevant-2026-11-04"
NEWS_RELATION = "news_mention"
INVALID_REASON = "title does not match the market's topic under the current rule"


@dataclass(frozen=True)
class LinkReview:
    link_id: int
    market_id: str
    question: str
    title: str | None
    topic: str | None
    keep: bool


def review_news_links(
    session: Session, now: datetime, gazetteer: Gazetteer, rules: ResearchRules
) -> list[LinkReview]:
    """Judge every active news link again. Reads only."""
    rows = session.execute(
        select(IntelMarketLinkRow, IntelObservationRow.title, MarketRow)
        .join(
            IntelObservationRow,
            IntelObservationRow.observation_id == IntelMarketLinkRow.observation_id,
        )
        .join(MarketRow, MarketRow.id == IntelMarketLinkRow.market_id)
        .where(IntelMarketLinkRow.relation == NEWS_RELATION, active_link())
        .order_by(IntelMarketLinkRow.id)
    ).all()
    reviews = []
    for link, title, market in rows:
        topic = topic_for(
            analyze_market(market.id, market.question, market.close_at, now, gazetteer, rules)
        )
        keep = topic is not None and title is not None and topic_relevant(title, topic)
        reviews.append(
            LinkReview(
                link_id=link.id,
                market_id=market.id,
                question=market.question,
                title=title,
                topic=topic.key if topic else None,
                keep=keep,
            )
        )
    return reviews


def invalidate_links(session: Session, reviews: list[LinkReview], now: datetime) -> int:
    """Flag the links a review rejected (the caller commits)."""
    count = 0
    for review in reviews:
        if review.keep:
            continue
        row = session.get(IntelMarketLinkRow, review.link_id)
        if row is None or row.invalidated_at is not None:
            continue
        row.invalidated_at = now
        row.invalidated_reason = INVALID_REASON
        row.invalidated_by = LINK_RULE_VERSION
        count += 1
    return count
