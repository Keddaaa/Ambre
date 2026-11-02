"""Isolation: the Cognitive Vault cannot reach the bet path, and the engines cannot depend on it."""

import ast
from pathlib import Path

import pytest

from ambre.cognitive import orm as cog_orm
from ambre.cognitive.models import ActionType
from ambre.infrastructure.orm import Base

SRC = Path(__file__).resolve().parents[2] / "src" / "ambre"
COGNITIVE = SRC / "cognitive"
PURE = ["models", "constraints", "lifecycle", "markdown"]
BET_PATH = (
    "ambre.execution_engine",
    "ambre.portfolio",
    "ambre.risk_engine",
    "ambre.survival_engine",
    "ambre.strategy_engine",
    "ambre.pipeline",
    "ambre.integrations",
    "ambre.data_engine",
)


def imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("name", PURE)
def test_pure_modules_touch_neither_database_nor_network(name: str) -> None:
    bad = {
        m
        for m in imports(COGNITIVE / f"{name}.py")
        if m.startswith(("ambre.infrastructure", "sqlalchemy", "httpx", "ambre.intelligence"))
    }
    assert bad == set(), f"{name}.py imports {sorted(bad)}"


def test_no_cognitive_module_can_reach_the_execution_risk_or_portfolio_path() -> None:
    for path in COGNITIVE.rglob("*.py"):
        bad = {m for m in imports(path) if m.startswith(BET_PATH)}
        assert bad == set(), f"{path.name} reaches the bet path through {sorted(bad)}"


def test_the_quantitative_engines_do_not_depend_on_the_vault() -> None:
    engines = [
        "feature_engine",
        "prediction_engine",
        "value_engine",
        "strategy_engine",
        "risk_engine",
        "survival_engine",
        "evaluation_engine",
        "execution_engine",
        "portfolio",
    ]
    for engine in engines:
        for path in (SRC / engine).rglob("*.py"):
            bad = {m for m in imports(path) if m.startswith("ambre.cognitive")}
            assert bad == set(), f"{path} depends on {sorted(bad)}"


def test_vault_tables_have_no_foreign_key_into_the_bet_path() -> None:
    protected = {
        "decisions",
        "positions",
        "ledger",
        "simulations",
        "portfolio_snapshots",
        "control_state",
    }
    for name, table in Base.metadata.tables.items():
        if name not in {t.__tablename__ for t in vault_models()}:
            continue
        targets = {fk.column.table.name for fk in table.foreign_keys}
        assert not (targets & protected), f"{name} references {sorted(targets & protected)}"


def vault_models() -> list[type]:
    return [
        cog_orm.CognitiveNoteRow,
        cog_orm.CognitiveNoteVersionRow,
        cog_orm.KnowledgeEdgeRow,
        cog_orm.RuleProposalRow,
        cog_orm.RuleVersionRow,
        cog_orm.CognitiveEventRow,
    ]


def test_no_rule_action_can_place_an_order_or_touch_a_secret_or_the_mode() -> None:
    forbidden = (
        "bet",
        "order",
        "trade",
        "execute",
        "halt",
        "mode",
        "key",
        "secret",
        "resume",
        "sell",
        "buy",
    )
    for action in ActionType:
        assert not any(word in action.value for word in forbidden), action.value
