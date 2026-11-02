import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ambre.integrations.manifold.mapping import MappedMarket, Rejected, map_market

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "manifold"
OBSERVED = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
SOURCE = "manifold:test"


def _load(name: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((FIXTURES / name).read_text())
    return data


def _synthetic(market_id: str) -> dict[str, Any]:
    return next(m for m in _load("synthetic_edge_cases.json") if m["id"] == market_id)


def test_map_real_page_binary_only() -> None:
    raws = _load("markets_updated_page.json")
    results = [map_market(raw, OBSERVED, SOURCE) for raw in raws]
    for raw, result in zip(raws, results, strict=True):
        if raw["outcomeType"] == "BINARY":
            assert isinstance(result, MappedMarket), (raw["id"], result)
        else:
            assert isinstance(result, Rejected)
            assert result.reason == f"unsupported outcomeType: {raw['outcomeType']}"
            assert result.outcome_type == raw["outcomeType"]
            assert result.kind == "unsupported"
            assert result.market_id == raw["id"]
    rejected_types = {r.outcome_type for r in results if isinstance(r, Rejected)}
    assert {"MULTIPLE_CHOICE", "PERP"} <= rejected_types


def test_map_real_binary_fields() -> None:
    raw = next(m for m in _load("markets_updated_page.json") if m["id"] == "sZzIqycRcS")
    mapped = map_market(raw, OBSERVED, SOURCE)
    assert isinstance(mapped, MappedMarket)
    m, s = mapped.market, mapped.snapshot
    assert m.id == "sZzIqycRcS"
    assert m.is_resolved is True
    assert m.resolution == "YES"
    assert m.created_at == datetime.fromtimestamp(raw["createdTime"] / 1000, tz=UTC)
    assert m.created_at.tzinfo is not None
    assert s.market_id == m.id
    assert s.pool_yes == raw["pool"]["YES"]
    assert s.pool_no == raw["pool"]["NO"]
    assert s.observed_at == OBSERVED
    assert s.source_id == SOURCE
    assert s.event_at == datetime.fromtimestamp(raw["lastUpdatedTime"] / 1000, tz=UTC)


def test_map_missing_optional_fields_none() -> None:
    mapped = map_market(_synthetic("synth_no_optional"), OBSERVED, SOURCE)
    assert isinstance(mapped, MappedMarket)
    assert mapped.snapshot.event_at is None
    assert mapped.snapshot.unique_bettor_count is None
    assert mapped.market.token is None
    assert mapped.market.resolution is None
    assert mapped.market.resolution_probability is None
    assert mapped.market.resolution_at is None
    assert mapped.market.group_slugs == []


def test_map_missing_pool_rejected() -> None:
    result = map_market(_synthetic("synth_no_pool"), OBSERVED, SOURCE)
    assert isinstance(result, Rejected)
    assert result.market_id == "synth_no_pool"
    assert "pool" in result.reason
    assert result.kind == "invalid"
    assert result.outcome_type == "BINARY"


def test_map_mkt_resolution() -> None:
    mapped = map_market(_synthetic("synth_resolved_mkt"), OBSERVED, SOURCE)
    assert isinstance(mapped, MappedMarket)
    assert mapped.market.resolution == "MKT"
    assert mapped.market.resolution_probability == 0.7
    assert mapped.market.resolution_at == datetime.fromtimestamp(1791500000, tz=UTC)


def test_map_cancel_resolution() -> None:
    mapped = map_market(_synthetic("synth_resolved_cancel"), OBSERVED, SOURCE)
    assert isinstance(mapped, MappedMarket)
    assert mapped.market.resolution == "CANCEL"
    assert mapped.market.resolution_probability is None


def test_map_cash_token_rejected() -> None:
    result = map_market(_synthetic("synth_cash_token"), OBSERVED, SOURCE)
    assert isinstance(result, Rejected)
    assert result.reason == "unsupported token: CASH"
    assert result.kind == "unsupported"


def test_map_wrong_mechanism_rejected() -> None:
    raw = deepcopy(_synthetic("synth_no_optional"))
    raw["mechanism"] = "dpm-2"
    result = map_market(raw, OBSERVED, SOURCE)
    assert isinstance(result, Rejected)
    assert result.reason == "unsupported mechanism: dpm-2"
    assert result.kind == "unsupported"


@pytest.mark.parametrize("field", ["id", "question", "createdTime", "p", "probability"])
def test_map_missing_required_field_rejected(field: str) -> None:
    raw = deepcopy(_synthetic("synth_no_optional"))
    del raw[field]
    result = map_market(raw, OBSERVED, SOURCE)
    assert isinstance(result, Rejected)
    assert field in result.reason


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("probability", "0.5"),
        ("probability", 1.5),
        ("p", 0),
        ("createdTime", "yesterday"),
        ("createdTime", True),
        ("pool", {"YES": 0, "NO": 10}),
        ("pool", {"YES": None, "NO": 10}),
        ("pool", [1, 2]),
    ],
)
def test_map_invalid_value_rejected(field: str, value: Any) -> None:
    raw = deepcopy(_synthetic("synth_no_optional"))
    raw[field] = value
    result = map_market(raw, OBSERVED, SOURCE)
    assert isinstance(result, Rejected)
    assert result.market_id == "synth_no_optional"


def test_map_rejects_without_id_has_none_market_id() -> None:
    raw = deepcopy(_synthetic("synth_no_optional"))
    del raw["id"]
    result = map_market(raw, OBSERVED, SOURCE)
    assert isinstance(result, Rejected)
    assert result.market_id is None


def test_map_out_of_range_close_time_means_no_close_date() -> None:
    raw = deepcopy(_synthetic("synth_no_optional"))
    raw["closeTime"] = 253402329540000.0  # year 9999: Manifold's "never closes" sentinel
    mapped = map_market(raw, OBSERVED, SOURCE)
    assert isinstance(mapped, MappedMarket)
    assert mapped.market.close_at is None


@pytest.mark.parametrize("field", ["createdTime", "resolutionTime"])
def test_map_out_of_range_other_timestamps_still_rejected(field: str) -> None:
    raw = deepcopy(_synthetic("synth_no_optional"))
    raw[field] = 253402329540000.0
    assert isinstance(map_market(raw, OBSERVED, SOURCE), Rejected)
