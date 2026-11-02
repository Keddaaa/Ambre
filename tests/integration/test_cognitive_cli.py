"""`ambre vault-export` after a real Alembic migration: proves migration 0004 and the export."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from ambre import cli
from ambre.cognitive import vault
from ambre.cognitive.models import NoteDraft, NoteType, Provenance
from ambre.domain.models import Actor
from ambre.infrastructure.db import make_engine, make_session_factory

T = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path / 'ambre.db'}"
    for key in list(os.environ):
        if key.startswith(("AMBRE_", "MANIFOLD_", "EXECUTION_", "ENABLE_", "DATABASE_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.chdir(tmp_path)
    return url


def test_migration_creates_the_vault_tables_and_export_writes_markdown(
    db_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["init-db"]) == 0  # real Alembic upgrade head, including 0004
    engine = make_engine(db_url)
    try:
        with make_session_factory(engine)() as s:
            vault.create_note(
                s,
                NoteDraft(
                    title="Announcement is not availability",
                    type=NoteType.LESSON,
                    content="Read the resolution criteria first.",
                    provenance=Provenance(created_by=Actor.AMBRE, source_refs=("decision:d1",)),
                    context={"domain": "technology"},
                    note_id="knowledge-tech-001",
                ),
                T,
            )
            s.commit()
    finally:
        engine.dispose()
    capsys.readouterr()

    out_dir = tmp_path / "vault-md"
    assert cli.main(["vault-export", "--dir", str(out_dir)]) == 0
    report = json.loads(capsys.readouterr().out.splitlines()[1])
    assert report == {"exported": 1, "directory": str(out_dir)}
    text = (out_dir / "knowledge-tech-001.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---\n", 2)[1])
    assert meta["type"] == "LESSON" and meta["confidence"] is None
    assert "# Announcement is not availability" in text
