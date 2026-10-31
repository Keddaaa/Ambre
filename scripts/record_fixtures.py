"""Record real Manifold market fixtures (read-only GET requests, run manually)."""

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://api.manifold.markets"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "manifold"
MAX_EXTRA_PAGES = 5


def _clean(market: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in market.items() if k != "creatorAvatarUrl"}


def _get(http: httpx.Client, params: dict[str, Any]) -> list[dict[str, Any]]:
    resp = http.get("/v0/markets", params=params)
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, list):
        raise SystemExit(f"unexpected response body: {type(body).__name__}")
    return body


def _wanted() -> dict[str, Any]:
    """Named predicates the recorded file must satisfy."""
    return {
        "BINARY resolved YES": lambda m: _is_resolved_binary(m, "YES"),
        "BINARY resolved NO": lambda m: _is_resolved_binary(m, "NO"),
        "BINARY": lambda m: m.get("outcomeType") == "BINARY",
        "MULTIPLE_CHOICE": lambda m: m.get("outcomeType") == "MULTIPLE_CHOICE",
        "PERP": lambda m: m.get("outcomeType") == "PERP",
    }


def _is_resolved_binary(m: dict[str, Any], resolution: str) -> bool:
    return (
        m.get("outcomeType") == "BINARY"
        and m.get("isResolved") is True
        and m.get("resolution") == resolution
    )


def main() -> int:
    params: dict[str, Any] = {"limit": 50, "sort": "updated-time", "order": "desc"}
    wanted = _wanted()
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as http:
        page = _get(http, params)
        markets = [_clean(m) for m in page]
        cursor = page[-1]["id"] if page else None
        pages = 0

        def missing() -> list[str]:
            return [n for n, f in wanted.items() if not any(f(m) for m in markets)]

        # Page the feed (up to 5 extra pages) until every wanted kind has been seen.
        while missing() and cursor is not None and pages < MAX_EXTRA_PAGES:
            pages += 1
            big = _get(http, {**params, "limit": 1000, "before": cursor})
            if not big:
                break
            cursor = big[-1]["id"]
            for name in missing():
                found = next((m for m in big if wanted[name](m)), None)
                if found is not None:
                    markets.append(_clean(found))
        lacking = missing()

    counts = Counter(m.get("outcomeType") for m in markets)
    for outcome_type, n in sorted(counts.items(), key=lambda kv: str(kv[0])):
        print(f"{outcome_type}: {n}")
    if lacking:
        print(f"missing from recorded file: {lacking}", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "markets_updated_page.json").write_text(json.dumps(markets, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
