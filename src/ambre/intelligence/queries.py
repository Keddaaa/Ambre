"""Research topics and queries for markets that have no specialised connector (pure).

A topic says *what to look up* for a market: a primary entity and the market's own content
words. Markets about the same entity in the same domain share one topic key, so one search can
serve them all; each market keeps its own terms, so the evidence is judged per market and
resolution criteria are never merged.

No model is involved: the topic comes from the analysed question and a fixed stop-word list.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ambre.intelligence.resolution import MarketDomain, ResolutionAnalysis, load_rules

MAX_GENERAL_TERMS = 3
MAX_GROUP_TERMS = 2
MIN_WORD = 4
MIN_ENTITYLESS_WORD = 5  # without an entity only longer, more specific words are searched
_STOP = frozenset(
    [
        "a",
        "about",
        "after",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "before",
        "being",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "might",
        "more",
        "most",
        "not",
        "of",
        "on",
        "or",
        "other",
        "our",
        "over",
        "than",
        "that",
        "the",
        "their",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "under",
        "until",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "new",
        "by",
    ]
)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-']*")
_LEXICON: dict[MarketDomain, frozenset[str]] | None = None


def _lexicon() -> dict[MarketDomain, frozenset[str]]:
    global _LEXICON
    if _LEXICON is None:
        _LEXICON = {
            d: frozenset(w for w in ws if " " not in w)
            for d, ws in load_rules().domain_keywords.items()
        }
    return _LEXICON


@dataclass(frozen=True)
class Topic:
    key: str
    domain: MarketDomain
    primary: str | None
    terms: tuple[str, ...]


def _content_terms(analysis: ResolutionAnalysis, primary: str | None) -> tuple[str, ...]:
    entity_words = {w.lower() for e in analysis.entities for w in e.split()}
    words = [m.group(0) for m in _WORD.finditer(analysis.subject)]
    lowered = [w.lower() for w in words]
    domain_words = _lexicon().get(analysis.domain, frozenset())
    ordered: list[str] = []
    # words of the domain lexicon first (they carry the event), then the other significant words
    for w in lowered:
        if w in domain_words and w not in ordered and w not in _STOP and w not in entity_words:
            ordered.append(w)
    for w in lowered:
        if (
            len(w) >= MIN_WORD
            and w not in _STOP
            and w not in entity_words
            and w not in ordered
            and not w.isdigit()
        ):
            ordered.append(w)
    return tuple(ordered if primary is not None else ordered[:5])


def _primary(entities: Sequence[str]) -> str | None:
    """The most telling entity: the first of at least 4 letters, else 3, else 2 (so a bare "US"
    does not beat "Pacific")."""
    for minimum in (4, 3, 2):
        found = next((e for e in entities if len(e) >= minimum), None)
        if found is not None:
            return found
    return None


def topic_for(analysis: ResolutionAnalysis) -> Topic | None:
    """The topic of a market, or ``None`` when the question holds nothing searchable."""
    primary = _primary(analysis.entities)
    terms = _content_terms(analysis, primary)
    domain = analysis.domain.value.lower()
    if primary is not None:
        return Topic(f"{domain}|{primary.lower()}", analysis.domain, primary, terms)
    significant = tuple(t for t in terms if len(t) >= MIN_ENTITYLESS_WORD)
    if len(significant) < 2:
        return None
    chosen = significant[:MAX_GENERAL_TERMS]
    return Topic(f"general|{'+'.join(sorted(chosen))}", analysis.domain, None, chosen)


def group_search(topics: Sequence[Topic]) -> str:
    """The search string for markets sharing a topic key: the entity plus the terms they all
    have in common (up to two when several markets share it, the market's own when alone)."""
    first = topics[0]
    shared = [t for t in first.terms if all(t in other.terms for other in topics)]
    if len(topics) > 1:
        shared = shared[:MAX_GROUP_TERMS]
    if first.primary is None:
        return " ".join(shared or list(first.terms))
    entity = f'"{first.primary}"' if " " in first.primary else first.primary
    return " ".join([entity, *shared])


def _has(text: str, word: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", text, re.IGNORECASE) is not None


#: Words that appear in market questions to state a condition, not a subject. A headline that
#: shares only one of them with a market is about something else ("close out October" is not
#: about a stock closing below a price), so they never decide relevance on their own.
GENERIC_TERMS = frozenset(
    {
        "above", "again", "another", "anyday", "below", "between", "close", "closed", "closes",
        "during", "ever", "every", "exceed", "exceeds", "least", "less", "reach", "reaches",
        "than", "through", "times", "under", "until", "within",
    }
)  # fmt: skip


def _specific_terms(terms: Sequence[str], *, at_least: int) -> Sequence[str]:
    """The terms that carry the subject. When too few remain, the market's own terms are kept
    as they are: dropping them would make the rule looser, never stricter."""
    specific = [t for t in terms if t not in GENERIC_TERMS]
    return specific if len(specific) >= at_least else terms


def _mentions(title: str, term: str) -> bool:
    return (
        _has(title, term)
        or _has(title, term.rstrip("s"))
        or _has(title, term + "s")
        or _has(title, term + "es")
    )


def topic_relevant(title: str, topic: Topic) -> bool:
    """Is an article title about this market's topic? The entity must be present together with
    one of the market's own subject terms; without an entity, at least two terms must appear.
    A generic condition word (see ``GENERIC_TERMS``) does not count when the market has
    subject terms of its own."""
    if not title.strip():
        return False
    if topic.primary is not None:
        if not _has(title, topic.primary):
            return False
        terms = _specific_terms(topic.terms, at_least=1)
        return not terms or any(_mentions(title, t) for t in terms)
    terms = _specific_terms(topic.terms, at_least=2)
    return sum(1 for t in terms if _has(title, t)) >= 2
