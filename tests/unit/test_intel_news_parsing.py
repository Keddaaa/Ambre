import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ambre.intelligence.models import (
    NewsArticlePayload,
    Observation,
    ResultKind,
    VerificationStatus,
)
from ambre.intelligence.news_parsing import (
    FeedParseError,
    canonical_url,
    clean_text,
    parse_feed,
    parse_snapi_articles,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "news"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _payload(obs: Observation) -> NewsArticlePayload:
    assert isinstance(obs.payload, NewsArticlePayload)
    return obs.payload


def _snapi() -> dict:  # type: ignore[type-arg]
    body: dict = json.loads((FIX / "synthetic_snapi_page.json").read_text(encoding="utf-8"))  # type: ignore[type-arg]
    return body


def test_snapi_keeps_the_four_clocks_apart_and_never_calls_a_report_a_fact() -> None:
    result = parse_snapi_articles(_snapi(), NOW)
    assert result.kind is ResultKind.OK and len(result.observations) == 4
    assert result.invalid_items == 1  # the item without a link
    first = result.observations[0]
    assert first.published_at == datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    assert first.available_at == NOW and first.ingested_at == NOW  # first read, not first printed
    assert first.event_at is None  # a headline does not date the event
    assert first.verification_status is VerificationStatus.UNVERIFIED
    p = _payload(first)
    assert p.summary == "SpaceX transferred propellant between two Starship vehicles in orbit."
    assert p.source_name == "Spaceflight Now" and p.domain == "spaceflight-now.example"


def test_snapi_unknown_dates_and_empty_summaries_stay_none() -> None:
    last = parse_snapi_articles(_snapi(), NOW).observations[-1]
    assert last.published_at is None  # "not a date" is not invented into one
    assert _payload(last).summary is None  # empty is absent, not ""


@pytest.mark.parametrize("body", [None, [], {}, {"results": "x"}, {"count": 0}])
def test_snapi_unexpected_bodies_are_inconclusive_not_empty(body: object) -> None:
    assert parse_snapi_articles(body, NOW).kind is ResultKind.INCONCLUSIVE


def test_snapi_empty_page_is_a_valid_empty_answer() -> None:
    assert parse_snapi_articles({"results": []}, NOW).kind is ResultKind.EMPTY


def test_the_same_article_is_the_same_observation() -> None:
    a = parse_snapi_articles(_snapi(), NOW).observations
    b = parse_snapi_articles(_snapi(), NOW).observations
    assert [o.observation_id for o in a] == [o.observation_id for o in b]
    assert len({o.observation_id for o in a}) == len(a)


def test_rss_items_need_a_link_and_a_title_and_dates_are_never_invented() -> None:
    result = parse_feed((FIX / "synthetic_rss.xml").read_bytes(), "feed_a", NOW, "World News")
    assert result.kind is ResultKind.OK and len(result.observations) == 2
    assert result.invalid_items == 1
    dated, undated = result.observations
    assert dated.published_at == datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
    assert undated.published_at is None and undated.available_at == NOW
    assert _payload(dated).summary == "The bank kept its benchmark rate unchanged."
    assert _payload(dated).source_name == "World News"


def test_atom_prefers_the_alternate_link_and_published_over_updated() -> None:
    result = parse_feed((FIX / "synthetic_atom.xml").read_bytes(), "feed_b", NOW)
    first, second = result.observations
    assert first.source_url == "https://atom.example/politics/budget"
    assert first.published_at == datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    assert second.published_at == datetime(2026, 11, 1, 11, 30, tzinfo=UTC)  # updated, no published
    assert _payload(second).summary == "Body used when no summary exists."


@pytest.mark.parametrize(
    "doc",
    [
        b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x "boom">]><rss version="2.0"/>',
        b'<?xml version="1.0"?><!DOCTYPE foo><rss version="2.0"><channel/></rss>',
        b"<rss><channel><item><title>&xxe;</title></item></channel></rss><!ENTITY xxe 'a'>",
        b"<html><body>not a feed</body></html>",
        b"<rss><unclosed>",
        b"",
    ],
)
def test_hostile_or_foreign_documents_are_refused(doc: bytes) -> None:
    with pytest.raises(FeedParseError):
        parse_feed(doc, "feed_x", NOW)


def test_oversized_documents_are_refused() -> None:
    with pytest.raises(FeedParseError):
        parse_feed(b"<rss>" + b"x" * 2_100_000 + b"</rss>", "feed_x", NOW)


def test_empty_feed_is_a_valid_empty_answer() -> None:
    xml = b'<rss version="2.0"><channel><title>t</title></channel></rss>'
    assert parse_feed(xml, "feed_x", NOW).kind is ResultKind.EMPTY


def test_clean_text_strips_markup_and_caps_without_inventing() -> None:
    assert clean_text("<p>Hi&nbsp;<b>there</b> &amp; you</p>", 100) == "Hi there & you"
    assert clean_text("   ", 10) is None and clean_text(None, 10) is None
    assert clean_text("abcdef", 3) == "abc"


def test_canonical_url_ignores_tracking_fragment_case_and_trailing_slash() -> None:
    a = canonical_url("HTTPS://News.Example/Path/story/?utm_source=x&id=7#top")
    b = canonical_url("https://news.example/Path/story?id=7")
    assert a == b
    assert canonical_url("https://news.example/a?id=1") != canonical_url(
        "https://news.example/a?id=2"
    )
