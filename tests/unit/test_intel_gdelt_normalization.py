"""GDELT DOC 2.0 article lists -> news observations (pure). The shape was seen live on 2026-11-02;
every value here is hand-written and ids are prefixed synthetic_."""

from datetime import UTC, datetime, timedelta
from typing import Any

from ambre.intelligence.models import (
    Domain,
    NewsArticlePayload,
    ObservationKind,
    ResultKind,
    VerificationStatus,
    is_actionable,
)
from ambre.intelligence.normalization import parse_gdelt_articles

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def article(url: str = "https://example.invalid/a1", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "url": url,
        "url_mobile": "",
        "title": "OpenAI releases a new model",
        "seendate": "20261101T093000Z",
        "socialimage": "",
        "domain": "example.invalid",
        "language": "English",
        "sourcecountry": "United States",
    }
    base.update(kw)
    return base


def test_an_article_becomes_an_unverified_observation_with_honest_clocks() -> None:
    r = parse_gdelt_articles({"articles": [article()]}, NOW)
    assert r.kind is ResultKind.OK and len(r.observations) == 1
    o = r.observations[0]
    assert (o.provider_id, o.domain, o.kind) == ("gdelt", Domain.NEWS, ObservationKind.OBSERVATION)
    p = o.payload
    assert isinstance(p, NewsArticlePayload)
    assert p.url == "https://example.invalid/a1" and p.title == "OpenAI releases a new model"
    assert (p.domain, p.language, p.source_country) == (
        "example.invalid",
        "English",
        "United States",
    )
    expected = datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
    assert p.seen_at == expected
    assert o.available_at == expected  # when GDELT first saw it, not when it was published
    assert o.published_at is None and o.event_at is None  # GDELT gives neither
    assert o.ingested_at == NOW
    assert o.verification_status is VerificationStatus.UNVERIFIED and not is_actionable(o)
    assert o.source_reliability is None  # popularity or volume is not reliability


def test_the_same_url_is_the_same_observation_whatever_the_query() -> None:
    a = parse_gdelt_articles({"articles": [article()]}, NOW).observations[0]
    b = parse_gdelt_articles({"articles": [article()]}, NOW + timedelta(hours=3)).observations[0]
    assert a.observation_id == b.observation_id
    other = parse_gdelt_articles({"articles": [article("https://example.invalid/a2")]}, NOW)
    assert other.observations[0].observation_id != a.observation_id


def test_missing_fields_stay_none() -> None:
    bare = {"url": "https://example.invalid/x"}
    o = parse_gdelt_articles({"articles": [bare]}, NOW).observations[0]
    p = o.payload
    assert isinstance(p, NewsArticlePayload)
    assert p.title is None and p.language is None and p.source_country is None and p.seen_at is None
    assert o.available_at is None  # an unknown availability is never dated by guess


def test_a_bad_seendate_keeps_the_article_but_not_a_date() -> None:
    o = parse_gdelt_articles({"articles": [article(seendate="soon")]}, NOW).observations[0]
    assert o.available_at is None


def test_items_without_a_usable_url_are_counted_and_skipped() -> None:
    r = parse_gdelt_articles(
        {
            "articles": [
                article("https://example.invalid/ok"),
                article(""),
                article("not a url"),
                article("javascript:alert(1)"),
                "garbage",
            ]
        },
        NOW,
    )
    assert [o.payload.url for o in r.observations] == ["https://example.invalid/ok"]  # type: ignore[union-attr]
    assert r.invalid_items == 4


def test_an_empty_object_is_inconclusive_not_empty() -> None:
    r = parse_gdelt_articles({}, NOW)
    assert (
        r.kind is ResultKind.INCONCLUSIVE and r.observations == ()
    )  # GDELT answers {} for several causes


def test_an_empty_article_list_is_empty() -> None:
    assert parse_gdelt_articles({"articles": []}, NOW).kind is ResultKind.EMPTY


def test_other_bodies_are_inconclusive() -> None:
    for body in ([], None, "x", {"articles": "no"}, {"other": 1}):
        assert parse_gdelt_articles(body, NOW).kind is ResultKind.INCONCLUSIVE, body


def test_a_title_is_cut_at_a_sane_length() -> None:
    o = parse_gdelt_articles({"articles": [article(title="x" * 1000)]}, NOW).observations[0]
    assert isinstance(o.payload, NewsArticlePayload) and len(o.payload.title or "") <= 300
