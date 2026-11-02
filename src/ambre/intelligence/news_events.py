"""Grouping of news items into events (pure, no I/O).

Several outlets report one event, and one outlet can be read through several feeds. An *event*
here is a set of items that share a canonical URL, or whose titles say nearly the same thing
within a short time. It is a counting device (how many distinct stories, how many independent
sources), not a statement that the event happened: every item stays an unverified report.

Two items join when their normalised titles overlap enough (Jaccard on content words) and they
were published, or first available, within ``window_hours`` of each other. Different events
are never merged because they share an entity: the overlap must be about the title as a whole.
"""

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ambre.intelligence.news_parsing import canonical_url

DEFAULT_THRESHOLD = 0.6
DEFAULT_WINDOW_HOURS = 72.0
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "by",
        "with",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "will",
        "would",
        "after",
        "before",
        "over",
        "as",
        "from",
        "into",
        "its",
        "it",
        "this",
        "that",
        "new",
        "says",
        "say",
        "said",
    }
)


@dataclass(frozen=True)
class NewsItem:
    observation_id: str
    url: str
    title: str
    source: str
    at: datetime  # published_at when known, else available_at


@dataclass(frozen=True)
class EventCluster:
    event_id: str  # the smallest observation id of the members: stable for a given membership
    members: tuple[str, ...]
    sources: tuple[str, ...]
    first_at: datetime
    last_at: datetime
    representative_title: str


def title_tokens(title: str) -> frozenset[str]:
    return frozenset(w for w in _WORD.findall(title.lower()) if w not in _STOP and len(w) > 1)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class _Union:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def join(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def cluster_items(
    items: Sequence[NewsItem],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    window_hours: float = DEFAULT_WINDOW_HOURS,
) -> list[EventCluster]:
    """Group items into events. Deterministic: the result does not depend on input order."""
    ordered = sorted(items, key=lambda i: (i.at, i.observation_id))
    tokens = [title_tokens(i.title) for i in ordered]
    union = _Union(len(ordered))

    by_url: dict[str, int] = {}
    for idx, item in enumerate(ordered):
        key = canonical_url(item.url)
        if key in by_url:
            union.join(by_url[key], idx)
        else:
            by_url[key] = idx

    index: dict[str, list[int]] = defaultdict(list)
    for idx, toks in enumerate(tokens):
        candidates = {j for t in toks for j in index[t]}
        for j in candidates:
            gap = abs((ordered[idx].at - ordered[j].at).total_seconds()) / 3600.0
            if gap <= window_hours and _jaccard(toks, tokens[j]) >= threshold:
                union.join(j, idx)
        for t in toks:
            index[t].append(idx)

    groups: dict[int, list[int]] = defaultdict(list)
    for idx in range(len(ordered)):
        groups[union.find(idx)].append(idx)

    clusters: list[EventCluster] = []
    for members in groups.values():
        rows = [ordered[i] for i in members]
        first = min(rows, key=lambda r: (r.at, r.observation_id))
        clusters.append(
            EventCluster(
                event_id=min(r.observation_id for r in rows),
                members=tuple(sorted(r.observation_id for r in rows)),
                sources=tuple(sorted({r.source for r in rows})),
                first_at=first.at,
                last_at=max(r.at for r in rows),
                representative_title=first.title,
            )
        )
    return sorted(clusters, key=lambda c: (c.first_at, c.event_id))
