"""Resolution Intelligence Engine (pure, deterministic, no model call).

It reads only what is stored about a market (the question and its dates) and reports what the
question states, what it leaves open and what is missing. It never fills a gap with a guess:
no event date means no event date, a number with no stated direction stays unordered, and the
market's close date is kept apart from the date of the event. Marked ambiguities and missing
items are what later stages use to refuse to treat a market as ready for estimation.

The market description (where resolution criteria usually live) is not stored by the sync,
so every analysis says so explicitly.
"""

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from ambre.config.settings import DEFAULT_CONFIG_PATH
from ambre.intelligence.planning import Gazetteer, Region

DEFAULT_RULES_PATH = DEFAULT_CONFIG_PATH.parent / "research_rules.yaml"
DESCRIPTION_MISSING = "market description not available (only the question is stored)"


class MarketDomain(StrEnum):
    WEATHER = "WEATHER"
    EARTHQUAKE = "EARTHQUAKE"
    SPORT = "SPORT"
    ECONOMY = "ECONOMY"
    TECHNOLOGY = "TECHNOLOGY"
    FINANCE = "FINANCE"
    CRYPTO = "CRYPTO"
    GEOPOLITICS = "GEOPOLITICS"
    SCIENCE = "SCIENCE"
    HEALTH = "HEALTH"
    GENERAL = "GENERAL"


class Comparator(StrEnum):
    GT = ">"
    GE = ">="
    LT = "<"
    LE = "<="


class PeriodKind(StrEnum):
    ON = "on"
    BY = "by"
    BEFORE = "before"
    IN_YEAR = "in_year"
    YEAR_END = "year_end"
    BY_YEAR = "by_year"
    BEFORE_YEAR = "before_year"


_COMPLEMENT = {
    Comparator.GT: Comparator.LE,
    Comparator.GE: Comparator.LT,
    Comparator.LT: Comparator.GE,
    Comparator.LE: Comparator.GT,
}


@dataclass(frozen=True)
class NumericCondition:
    value: float
    unit: str | None
    comparator: Comparator | None
    text: str


@dataclass(frozen=True)
class EventPeriod:
    kind: PeriodKind
    start: date | None  # None = open start (a deadline, not a window)
    end: date
    text: str


@dataclass(frozen=True)
class ResearchRules:
    domain_keywords: dict[MarketDomain, tuple[str, ...]]
    reference_sources: tuple[str, ...]


@dataclass(frozen=True)
class ResolutionAnalysis:
    market_id: str
    question: str
    subject: str
    domain: MarketDomain
    entities: tuple[str, ...]
    location: Region | None
    period: EventPeriod | None
    closes_at: datetime | None
    conditions: tuple[NumericCondition, ...]
    yes_condition: str | None
    no_condition: str | None
    reference_sources: tuple[str, ...]
    ambiguities: tuple[str, ...] = field(default_factory=tuple)
    missing: tuple[str, ...] = field(default_factory=tuple)


def load_rules(path: Path | None = None) -> ResearchRules:
    data: dict[str, Any] = yaml.safe_load((path or DEFAULT_RULES_PATH).read_text(encoding="utf-8"))
    domains = {
        MarketDomain(name): tuple(str(w).lower() for w in words)
        for name, words in data["domains"].items()
    }
    return ResearchRules(domains, tuple(str(s) for s in data.get("reference_sources", [])))


# ----------------------------------------------------------------------------- numbers

_NUM = r"(\d+(?:\.\d+)?)"
_UNIT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(_NUM + r"\s*°\s?F", re.I), "°F"),
    (re.compile(_NUM + r"\s*°\s?C", re.I), "°C"),
    (re.compile(_NUM + r"\s*degrees?\s*(?:fahrenheit|F\b)", re.I), "°F"),
    (re.compile(_NUM + r"\s*degrees?\s*(?:celsius|C\b)", re.I), "°C"),
    (re.compile(_NUM + r"\s*degrees?\b", re.I), "degrees"),
    (re.compile(_NUM + r"\s*(?:%|percent\b)", re.I), "%"),
    (re.compile(_NUM + r"\s*(?:mm|millimet(?:er|re)s?)\b", re.I), "mm"),
    (re.compile(_NUM + r"\s*(?:inch(?:es)?)\b", re.I), "in"),
    (re.compile(_NUM + r"\s*cm\b", re.I), "cm"),
    (
        re.compile(
            _NUM + r"\s*(?:\+|or\s+(?:greater|more|higher|above|stronger))?\s*"
            r"(?:magnitude|earthquakes?|quakes?)\b",
            re.I,
        ),
        "magnitude",
    ),
    (re.compile(r"magnitude\s*(?:of\s*)?" + _NUM, re.I), "magnitude"),
]
_DOLLAR = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|k|m|b)?\b", re.I)
_SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9, "b": 1e9}
_AFTER_GE = re.compile(r"^\s*(?:\+|or\s+(?:more|greater|higher|above|stronger)\b)", re.I)
_AFTER_LE = re.compile(r"^\s*or\s+(?:less|lower|fewer|below)\b", re.I)
_BEFORE_PHRASES: list[tuple[re.Pattern[str], Comparator]] = [
    (re.compile(r"\b(?:at least|no less than|reach(?:es|ed)?|hits?)\s*$", re.I), Comparator.GE),
    (
        re.compile(
            r"\b(?:exceed(?:s|ed)?|above|over|more than|greater than|higher than|"
            r"surpass(?:es|ed)?|tops?)\s*$",
            re.I,
        ),
        Comparator.GT,
    ),
    (re.compile(r"\b(?:at most|no more than)\s*$", re.I), Comparator.LE),
    (
        re.compile(r"\b(?:below|under|less than|lower than|fewer than)\s*$", re.I),
        Comparator.LT,
    ),
]


def _comparator(question: str, start: int, end: int, matched: str) -> Comparator | None:
    if re.search(r"\+|or\s+(?:greater|more|higher|above|stronger)", matched, re.I):
        return Comparator.GE
    after = question[end : end + 25]
    if _AFTER_GE.match(after):
        return Comparator.GE
    if _AFTER_LE.match(after):
        return Comparator.LE
    before = question[max(0, start - 30) : start]
    for pattern, comparator in _BEFORE_PHRASES:
        if pattern.search(before):
            return comparator
    return None


def _conditions(question: str) -> list[NumericCondition]:
    found: list[tuple[int, NumericCondition]] = []
    taken: list[tuple[int, int]] = []

    def free(span: tuple[int, int]) -> bool:
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    for match in _DOLLAR.finditer(question):
        if not free(match.span()):
            continue
        value = float(match.group(1).replace(",", ""))
        scale = _SCALE.get((match.group(2) or "").lower(), 1.0)
        taken.append(match.span())
        cond = NumericCondition(
            value * scale,
            "USD",
            _comparator(question, *match.span(), match.group(0)),
            match.group(0),
        )
        found.append((match.start(), cond))
    for pattern, unit in _UNIT_PATTERNS:
        for match in pattern.finditer(question):
            if not free(match.span()):
                continue
            taken.append(match.span())
            cond = NumericCondition(
                float(match.group(1)),
                unit,
                _comparator(question, *match.span(), match.group(0)),
                match.group(0),
            )
            found.append((match.start(), cond))
    return [c for _, c in sorted(found, key=lambda item: item[0])]


# ------------------------------------------------------------------------------- dates

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_PREP = r"(?P<prep>on|by|before|until|through|no later than)"
_FULL_DATE = re.compile(
    rf"\b{_PREP}\s+(?P<m>{_MONTH_RE})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<y>(?:19|20)\d{{2}}))?\b",
    re.I,
)
_ISO_DATE = re.compile(
    rf"\b{_PREP}\s+(?P<y>(?:19|20)\d{{2}})-(?P<m>\d{{2}})-(?P<d>\d{{2}})\b", re.I
)
_YEAR_END = re.compile(r"\bby the end of\s+(?P<y>(?:19|20)\d{2})\b", re.I)
_IN_YEAR = re.compile(r"\bin\s+(?P<y>(?:19|20)\d{2})\b", re.I)
_BY_YEAR = re.compile(r"\b(?P<prep>by|before)\s+(?P<y>(?:19|20)\d{2})\b(?!-)", re.I)


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _period(question: str, ambiguities: list[str]) -> EventPeriod | None:
    candidates: list[EventPeriod] = []
    for match in _FULL_DATE.finditer(question):
        prep = match.group("prep").lower()
        if match.group("y") is None:
            ambiguities.append(f"year not stated in {match.group(0)!r}: the date is not guessed")
            continue
        d = _safe_date(
            int(match.group("y")), _MONTHS[match.group("m").lower()], int(match.group("d"))
        )
        if d is None:
            ambiguities.append(f"impossible date {match.group(0)!r}")
            continue
        if prep == "on":
            candidates.append(EventPeriod(PeriodKind.ON, d, d, match.group(0)))
        elif prep == "before":
            candidates.append(
                EventPeriod(PeriodKind.BEFORE, None, d - timedelta(days=1), match.group(0))
            )
        else:
            candidates.append(EventPeriod(PeriodKind.BY, None, d, match.group(0)))
    for match in _ISO_DATE.finditer(question):
        d = _safe_date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
        if d is None:
            ambiguities.append(f"impossible date {match.group(0)!r}")
        elif match.group("prep").lower() == "on":
            candidates.append(EventPeriod(PeriodKind.ON, d, d, match.group(0)))
        else:
            candidates.append(EventPeriod(PeriodKind.BY, None, d, match.group(0)))
    if not candidates:
        for match in _YEAR_END.finditer(question):
            y = int(match.group("y"))
            candidates.append(
                EventPeriod(PeriodKind.YEAR_END, None, date(y, 12, 31), match.group(0))
            )
    if not candidates:
        for match in _IN_YEAR.finditer(question):
            y = int(match.group("y"))
            candidates.append(
                EventPeriod(PeriodKind.IN_YEAR, date(y, 1, 1), date(y, 12, 31), match.group(0))
            )
    if not candidates:
        for match in _BY_YEAR.finditer(question):
            y = int(match.group("y"))
            if match.group("prep").lower() == "before":
                candidates.append(
                    EventPeriod(PeriodKind.BEFORE_YEAR, None, date(y - 1, 12, 31), match.group(0))
                )
            else:
                candidates.append(
                    EventPeriod(PeriodKind.BY_YEAR, None, date(y, 12, 31), match.group(0))
                )
                ambiguities.append(
                    f"year-level deadline {match.group(0)!r}: "
                    f"end of {y} assumed, start of {y} is possible"
                )
    if len({(c.kind, c.start, c.end) for c in candidates}) > 1:
        ambiguities.append("several dates in the question: the first one is used")
    return candidates[0] if candidates else None


# ----------------------------------------------------------------------------- the rest

_STOP_FIRST = {
    "Will", "Is", "Are", "Does", "Do", "Did", "Has", "Have", "Can", "Could", "Would", "Who",
    "What", "When", "Which", "If", "By", "In", "On", "Before", "After", "The", "A", "An",
    "According", "Per", "Any", "At",
}  # fmt: skip
_MONTH_WORDS = {m.capitalize() for m in _MONTHS} | {
    d for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
}
_ENTITY = re.compile(r"\b[A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*)*")
_SOURCE_PHRASE = re.compile(
    r"\b(?:according to|as reported by|as measured by|per|based on)\s+(?:the\s+)?"
    r"([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})"
)


def _entities(question: str) -> tuple[str, ...]:
    seen: list[str] = []
    for match in _ENTITY.finditer(question):
        words = [w for w in match.group(0).split() if w not in _MONTH_WORDS]
        while words and words[0] in _STOP_FIRST:
            words.pop(0)
        text = " ".join(words).strip(".")
        if len(text) >= 2 and text not in seen and text not in _STOP_FIRST:
            seen.append(text)
    return tuple(seen)


def _classify(
    question: str, conditions: list[NumericCondition], rules: ResearchRules, ambiguities: list[str]
) -> MarketDomain:
    scores: dict[MarketDomain, int] = {}
    lowered = question.lower()
    for domain, words in rules.domain_keywords.items():
        hits = sum(1 for w in words if re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", lowered))
        if hits:
            scores[domain] = hits
    if any(c.unit in ("°F", "°C") for c in conditions):
        scores[MarketDomain.WEATHER] = scores.get(MarketDomain.WEATHER, 0) + 1
    if not scores:
        return MarketDomain.GENERAL
    order = list(MarketDomain)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], order.index(kv[0])))
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        ambiguities.append(
            f"domain ambiguous: {ranked[0][0].value} and {ranked[1][0].value} match equally"
        )
    return ranked[0][0]


def _sources(question: str, rules: ResearchRules) -> tuple[str, ...]:
    found: list[str] = []
    for known in rules.reference_sources:
        if (
            re.search(rf"(?<![\w]){re.escape(known)}(?![\w])", question, re.I)
            and known not in found
        ):
            found.append(known)
    for match in _SOURCE_PHRASE.finditer(question):
        text = match.group(1)
        if text not in found and not any(text.lower() == k.lower() for k in found):
            found.append(text)
    return tuple(found)


def _criteria(
    conditions: list[NumericCondition], ambiguities: list[str]
) -> tuple[str | None, str | None]:
    if len(conditions) > 1:
        ambiguities.append("several numeric thresholds: no single YES/NO criterion is derived")
        return None, None
    if not conditions:
        return None, None
    c = conditions[0]
    if c.comparator is None:
        ambiguities.append(f"comparison direction not stated for {c.text!r}")
        return None, None
    unit = c.unit or ""
    spaced = f" {unit}" if unit in ("magnitude", "degrees", "USD", "mm", "cm", "in") else unit
    yes = f"the measured value is {c.comparator.value} {c.value:g}{spaced}"
    no = f"the measured value is {_COMPLEMENT[c.comparator].value} {c.value:g}{spaced}"
    return yes, no


def analyze_market(
    market_id: str,
    question: str,
    closes_at: datetime | None,
    now: datetime,
    gazetteer: Gazetteer,
    rules: ResearchRules,
) -> ResolutionAnalysis:
    ambiguities: list[str] = []
    missing: list[str] = [DESCRIPTION_MISSING]
    conditions = _conditions(question)
    domain = _classify(question, conditions, rules, ambiguities)
    period = _period(question, ambiguities)
    if period is None:
        missing.append("event date: the question gives none")
        ambiguities.append("the close date is not the event date and is not used instead")
    location = gazetteer.find(question)
    if closes_at is None:
        ambiguities.append("close time unknown (horizon unknown)")
    elif closes_at <= now:
        ambiguities.append("market already closed")
    sources = _sources(question, rules)
    if not sources:
        missing.append("resolution source: the question names none")
    yes, no = _criteria(conditions, ambiguities)
    return ResolutionAnalysis(
        market_id=market_id,
        question=question,
        subject=question.strip().rstrip("?").strip(),
        domain=domain,
        entities=_entities(question),
        location=location,
        period=period,
        closes_at=closes_at,
        conditions=tuple(conditions),
        yes_condition=yes,
        no_condition=no,
        reference_sources=sources,
        ambiguities=tuple(dict.fromkeys(ambiguities)),
        missing=tuple(missing),
    )
