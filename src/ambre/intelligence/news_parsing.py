"""Parsing of news sources into typed observations (pure, no I/O).

Two shapes: the Spaceflight News API v4 (JSON) and RSS 2.0 / Atom 1.0 feeds (XML). Both give a
title, a link, a short summary and dates, never the article text, so every item is a *report*
(``UNVERIFIED``), not a fact. The four clocks stay apart: ``published_at`` is what the source
states, ``available_at`` is when AMBRE could first read it (the fetch instant: a source that
gives no first-seen time cannot be trusted to have shown the item earlier), ``event_at`` stays
``None`` (a headline does not date the event) and ``ingested_at`` is the fetch instant.

XML from the network is untrusted: any document type declaration or entity is refused before
parsing, so entity-expansion and external-entity tricks never reach the parser.
"""

import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import ValidationError

from ambre.intelligence.models import (
    Domain,
    FetchResult,
    NewsArticlePayload,
    Observation,
    ObservationKind,
    ResultKind,
    VerificationStatus,
    make_observation_id,
)

MAX_TITLE = 300
MAX_SUMMARY = 500
MAX_ITEMS = 200
MAX_XML_BYTES = 2_000_000
SNAPI_ID = "spaceflight_news"

_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
_TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ocid", "cmpid")
_ATOM = "{http://www.w3.org/2005/Atom}"


def clean_text(value: object, limit: int) -> str | None:
    """Plain text of a title or summary: markup and entities removed, whitespace collapsed,
    capped. Empty or non-string input is ``None``, never an empty string."""
    if not isinstance(value, str):
        return None
    text = _SPACES.sub(" ", html.unescape(_TAGS.sub(" ", value))).strip()
    return text[:limit] if text else None


def canonical_url(url: str) -> str:
    """The same article under tracking parameters, fragments, a trailing slash or a different
    host case is one URL."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING)
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def _http_url(value: object) -> str | None:
    if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
        return value.strip()
    return None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _aware(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def parse_rfc822(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _aware(parsedate_to_datetime(value.strip()))
    except (TypeError, ValueError):
        return None


def _observation(
    provider_id: str,
    native_id: str,
    url: str,
    title: str,
    summary: str | None,
    source_name: str | None,
    published_at: datetime | None,
    ingested_at: datetime,
) -> Observation | None:
    try:
        payload = NewsArticlePayload(
            url=url,
            title=title,
            domain=urlsplit(url).netloc.lower() or None,
            summary=summary,
            source_name=source_name,
        )
    except ValidationError:
        return None
    return Observation(
        observation_id=make_observation_id(provider_id, native_id, None),
        provider_id=provider_id,
        domain=Domain.NEWS,
        kind=ObservationKind.OBSERVATION,
        payload=payload,
        ingested_at=ingested_at,
        verification_status=VerificationStatus.UNVERIFIED,
        title=title,
        source_url=url,
        published_at=published_at,
        available_at=ingested_at,
    )


def _result(observations: list[Observation], invalid: int) -> FetchResult:
    if observations:
        return FetchResult(
            kind=ResultKind.OK, observations=tuple(observations), invalid_items=invalid
        )
    if invalid:
        return FetchResult(
            kind=ResultKind.INCONCLUSIVE, detail="every item was invalid", invalid_items=invalid
        )
    return FetchResult(kind=ResultKind.EMPTY)


# --------------------------------------------------------------------------- Spaceflight News


def parse_snapi_articles(body: object, ingested_at: datetime) -> FetchResult:
    """Parse a Spaceflight News API v4 ``/v4/articles/`` page (``{"results": [...]}``)."""
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        return FetchResult(kind=ResultKind.INCONCLUSIVE, detail="unexpected response body")
    out: list[Observation] = []
    invalid = 0
    for raw in body["results"][:MAX_ITEMS]:
        item: dict[str, Any] = raw if isinstance(raw, dict) else {}
        url = _http_url(item.get("url"))
        title = clean_text(item.get("title"), MAX_TITLE)
        native = item.get("id")
        if url is None or title is None or not isinstance(native, int) or isinstance(native, bool):
            invalid += 1
            continue
        obs = _observation(
            SNAPI_ID,
            str(native),
            url,
            title,
            clean_text(item.get("summary"), MAX_SUMMARY),
            clean_text(item.get("news_site"), 100),
            parse_iso(item.get("published_at")),
            ingested_at,
        )
        if obs is None:
            invalid += 1
        else:
            out.append(obs)
    return _result(out, invalid)


# --------------------------------------------------------------------------- RSS / Atom


class FeedParseError(ValueError):
    """The document is not a feed this adapter accepts."""


def _text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    return "".join(element.itertext())


def _atom_link(entry: ET.Element) -> str | None:
    fallback: str | None = None
    for link in entry.findall(f"{_ATOM}link"):
        href = link.get("href")
        if link.get("rel", "alternate") == "alternate" and _http_url(href):
            return href
        fallback = fallback or (href if _http_url(href) else None)
    return fallback


def parse_feed(
    content: bytes, provider_id: str, ingested_at: datetime, source_name: str | None = None
) -> FetchResult:
    """Parse an RSS 2.0 or Atom 1.0 document. Raises ``FeedParseError`` if it is neither, is
    too large, or declares a document type or an entity."""
    if len(content) > MAX_XML_BYTES:
        raise FeedParseError(f"document larger than {MAX_XML_BYTES} bytes")
    head = content[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in content.lower():
        raise FeedParseError("document type declarations and entities are refused")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise FeedParseError(f"not well-formed XML: {exc}") from exc

    rows: list[tuple[str | None, str | None, str | None, str | None, datetime | None]] = []
    if root.tag == "rss" or root.tag.endswith("}RDF"):
        for item in root.iter("item"):
            guid = _text(item.find("guid"))
            link = _text(item.find("link"))
            rows.append(
                (
                    guid or link,
                    link,
                    _text(item.find("title")),
                    _text(item.find("description")),
                    parse_rfc822(_text(item.find("pubDate"))),
                )
            )
    elif root.tag == f"{_ATOM}feed":
        for entry in root.findall(f"{_ATOM}entry"):
            published = parse_iso(_text(entry.find(f"{_ATOM}published"))) or parse_iso(
                _text(entry.find(f"{_ATOM}updated"))
            )
            rows.append(
                (
                    _text(entry.find(f"{_ATOM}id")) or _atom_link(entry),
                    _atom_link(entry),
                    _text(entry.find(f"{_ATOM}title")),
                    _text(entry.find(f"{_ATOM}summary")) or _text(entry.find(f"{_ATOM}content")),
                    published,
                )
            )
    else:
        raise FeedParseError(f"neither RSS nor Atom (root element {root.tag!r})")

    out: list[Observation] = []
    invalid = 0
    for native, link, title_raw, summary_raw, published in rows[:MAX_ITEMS]:
        url = _http_url(link.strip() if link else None)
        title = clean_text(title_raw, MAX_TITLE)
        if url is None or title is None or not native or not native.strip():
            invalid += 1
            continue
        obs = _observation(
            provider_id,
            native.strip(),
            url,
            title,
            clean_text(summary_raw, MAX_SUMMARY),
            source_name,
            published,
            ingested_at,
        )
        if obs is None:
            invalid += 1
        else:
            out.append(obs)
    return _result(out, invalid)
