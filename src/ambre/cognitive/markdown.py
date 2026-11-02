"""Markdown export of a note (pure): controlled YAML frontmatter + readable body + wiki links.

The database stays the source of truth; this file is a human-readable view. Links in the
exported text are for reading only: the integrity of relations is enforced by the edge table.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import yaml

from ambre.cognitive.models import NoteRecord, RelationType


@dataclass(frozen=True)
class RelationView:
    type: RelationType
    other_id: str
    direction: str  # "outgoing" (this note -> other) or "incoming" (other -> this note)


def _link_title(title: str) -> str:
    return title.replace("[[", "(").replace("]]", ")").replace("\n", " ").strip()


def render_note(
    note: NoteRecord, relations: Sequence[RelationView], titles: Mapping[str, str]
) -> str:
    meta: dict[str, Any] = {
        "id": note.note_id,
        "type": note.type.value,
        "status": note.status.value,
        "version": note.version,
        "domain": note.context.get("domain"),
        "created_by": note.provenance.created_by.value,
        "created_at": note.created_at.isoformat(),
        "updated_at": note.updated_at.isoformat(),
        "valid_from": note.valid_from.isoformat() if note.valid_from else None,
        "valid_until": note.valid_until.isoformat() if note.valid_until else None,
        "evidence_level": note.evidence_level.value,
        "confidence": note.confidence,  # None stays null: never replaced by a number
        "source_refs": list(note.provenance.source_refs),
    }
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=False)
    parts = [f"---\n{front}---\n", f"# {note.title}\n", note.content.rstrip("\n") + "\n"]
    if relations:
        lines = ["## Relations", ""]
        for rel in relations:
            label = _link_title(titles.get(rel.other_id, rel.other_id))
            marker = " (incoming)" if rel.direction == "incoming" else ""
            lines.append(f"- {rel.type.value}{marker} [[{label}]]")
        parts.append("\n".join(lines) + "\n")
    return "\n".join(parts)
