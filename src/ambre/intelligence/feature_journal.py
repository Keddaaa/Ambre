"""Serialisation of dated context features for the feature journal (pure, no I/O).

The journal is the future training set: for each market, what the information network
*knew and had available* at a given instant, next to the market price at that instant.
Joined later with the resolved outcome, it allows a with/without-source comparison on
unseen markets. It is a record, never a signal: nothing here produces a probability.
"""

import hashlib
import json
from typing import Any

from ambre.intelligence.context import FeatureSet


def journal_payload(features: FeatureSet) -> list[dict[str, Any]]:
    """JSON-ready features sorted by name; an absent value stays ``None``, never 0."""
    return [
        {
            "name": f.name,
            "value": f.value,
            "unit": f.unit,
            "kind": f.kind,
            "provider_id": f.provider_id,
            "available_at": f.available_at.isoformat(),
            "observation_id": f.observation_id,
        }
        for f in sorted(features.features, key=lambda f: f.name)
    ]


#: Features that only grow with the clock (age of the newest datum). They stay in the journal
#: but must not make unchanged knowledge look new on every pass.
_CLOCK_DERIVED_SUFFIX = "age_seconds"


def fingerprint(payload: list[dict[str, Any]]) -> str:
    """Stable digest of what is known, used to avoid journaling the same knowledge twice."""
    stable = [p for p in payload if not str(p["name"]).endswith(_CLOCK_DERIVED_SUFFIX)]
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
