"""Dependency rule: the pure intelligence modules touch neither the database nor the network."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "src" / "ambre" / "intelligence"
PURE = [
    "models",
    "budget",
    "registry",
    "normalization",
    "planning",
    "value",
    "routing",
    "matching",
    "resolution",
    "domain_params",
    "generic_planning",
    "context",
    "estimation",
    "queries",
    "states",
    "feature_journal",
]
FORBIDDEN = ("ambre.infrastructure", "ambre.integrations", "ambre.pipeline", "httpx", "sqlalchemy")


def imported_modules(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("name", PURE)
def test_pure_module_has_no_database_or_network_import(name: str) -> None:
    bad = {m for m in imported_modules(ROOT / f"{name}.py") if m.startswith(FORBIDDEN)}
    assert bad == set(), f"{name}.py imports {sorted(bad)}"


def test_quantitative_engines_do_not_depend_on_the_intelligence_network() -> None:
    engines = [
        "feature_engine",
        "prediction_engine",
        "value_engine",
        "strategy_engine",
        "risk_engine",
        "survival_engine",
        "evaluation_engine",
    ]
    src = ROOT.parent
    for engine in engines:
        for path in (src / engine).rglob("*.py"):
            bad = {m for m in imported_modules(path) if m.startswith("ambre.intelligence")}
            assert bad == set(), f"{path.name} depends on {sorted(bad)}"


def test_the_network_never_imports_the_execution_or_portfolio_modules() -> None:
    for path in ROOT.rglob("*.py"):
        bad = {
            m
            for m in imported_modules(path)
            if m.startswith(("ambre.execution_engine", "ambre.portfolio", "ambre.risk_engine"))
        }
        assert bad == set(), f"{path.name} can reach the bet path through {sorted(bad)}"
