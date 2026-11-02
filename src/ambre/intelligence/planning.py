"""Information Planner: identify what a market lacks before any search (pure).

It reads only what is stored about the market (question, close time). The resolution
criteria are not stored, so they are always listed as unresolved: nothing it returns may be
treated as "the market's condition is met".
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ambre.intelligence.models import InfoNeed

MAG_MIN, MAG_MAX = 1.0, 10.0
_EARTHQUAKE = re.compile(r"\bearthquakes?\b", re.IGNORECASE)
# "6.7 or greater magnitude", "7.0 or greater earthquake", "magnitude 6.5": a number tied to a
# magnitude or earthquake word (so "100 or more deaths" is never read as a magnitude).
_MAG_PATTERNS = (
    re.compile(
        r"(\d+(?:\.\d+)?)\s*(?:\+|or\s+(?:greater|more|higher|above|stronger))\s+"
        r"(?:magnitude|earthquakes?|quakes?)",
        re.IGNORECASE,
    ),
    re.compile(r"magnitude\s*(?:of\s*)?(\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:\+\s*)?magnitude", re.IGNORECASE),
)


def _has_word(text: str, alias: str) -> bool:
    """Whole-word, case-insensitive match: "paris" is not found inside "comparison"."""
    return re.search(rf"(?<![\w]){re.escape(alias)}(?![\w])", text, re.IGNORECASE) is not None


@dataclass(frozen=True)
class Region:
    """A named area. ``bbox`` = (min_lat, max_lat, min_lon, max_lon) is a modelling choice
    written by hand in configs/gazetteer.yaml, not an official boundary."""

    name: str
    aliases: tuple[str, ...]
    bbox: tuple[float, float, float, float]
    #: (latitude, longitude) of a point to query for point-based data such as forecasts
    center: tuple[float, float] | None = None
    timezone: str | None = None  # IANA name, needed to read a "calendar day" correctly

    def contains(self, lat: float | None, lon: float | None) -> bool:
        if lat is None or lon is None:
            return False
        min_lat, max_lat, min_lon, max_lon = self.bbox
        return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon

    def matches_text(self, text: str | None) -> bool:
        if not text:
            return False
        return any(_has_word(text, alias) for alias in self.aliases)


class Gazetteer:
    def __init__(self, regions: Sequence[Region]) -> None:
        self._regions = list(regions)

    def find(self, text: str) -> Region | None:
        """The region whose longest alias appears in ``text`` (longest wins)."""
        best: tuple[int, Region] | None = None
        for region in self._regions:
            for alias in region.aliases:
                if _has_word(text, alias) and (best is None or len(alias) > best[0]):
                    best = (len(alias), region)
        return best[1] if best else None


def parse_gazetteer(document: Mapping[str, Any]) -> Gazetteer:
    regions = document.get("regions")
    if not isinstance(regions, Mapping):
        raise ValueError("gazetteer needs a 'regions' mapping")
    out: list[Region] = []
    for name, raw in regions.items():
        aliases = tuple(str(a).lower() for a in raw["aliases"])
        bbox = tuple(float(x) for x in raw["bbox"])
        if (
            len(bbox) != 4
            or not (-90 <= bbox[0] <= bbox[1] <= 90)
            or not (-180 <= bbox[2] <= bbox[3] <= 180)
        ):
            raise ValueError(f"gazetteer region {name}: invalid bbox {bbox}")
        raw_center = raw.get("center")
        center = (float(raw_center[0]), float(raw_center[1])) if raw_center else None
        if center is not None and not (-90 <= center[0] <= 90 and -180 <= center[1] <= 180):
            raise ValueError(f"gazetteer region {name}: invalid center {center}")
        tz = raw.get("timezone")
        out.append(
            Region(
                name=str(name),
                aliases=aliases,
                bbox=bbox,
                center=center,
                timezone=str(tz) if tz else None,
            )
        )
    return Gazetteer(out)


def load_gazetteer(path: Path) -> Gazetteer:
    return parse_gazetteer(yaml.safe_load(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class Gap:
    market_id: str
    need: InfoNeed
    objective: str
    min_magnitude: float | None
    region: Region | None
    reasons: tuple[str, ...]
    unresolved: tuple[str, ...]


def _magnitude(question: str) -> float | None:
    for pattern in _MAG_PATTERNS:
        for match in pattern.finditer(question):
            value = float(match.group(1))
            if MAG_MIN <= value <= MAG_MAX:
                return value
    return None


def plan_gaps(
    market_id: str,
    question: str,
    close_at: datetime | None,
    now: datetime,
    gazetteer: Gazetteer,
) -> Gap | None:
    """The information gap of one market, or ``None`` when no provider need applies."""
    if close_at is not None and close_at <= now:
        return None
    if not _EARTHQUAKE.search(question):
        return None
    threshold = _magnitude(question)
    region = gazetteer.find(question)
    unresolved = ["resolution criteria not read (the market description is not stored)"]
    if threshold is None:
        unresolved.append("magnitude threshold not stated in the question")
    if region is None:
        unresolved.append("region not recognised by the gazetteer")
    if close_at is None:
        unresolved.append("close time unknown (horizon unknown)")
    where = region.name if region else "any region"
    what = f"magnitude >= {threshold:g}" if threshold is not None else "any magnitude"
    return Gap(
        market_id=market_id,
        need=InfoNeed.RECENT_EARTHQUAKES,
        objective=f"observe earthquakes ({what}) in {where} that could resolve this market",
        min_magnitude=threshold,
        region=region,
        reasons=("question mentions an earthquake",),
        unresolved=tuple(unresolved),
    )
