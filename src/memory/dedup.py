"""LLM-based duplicate detection for entity upserts.

Exact name matching (the default in StructuredStore.upsert_*) misses
semantic duplicates: 'TFG SVD' / 'Anatomía Emocional de BERT' /
'Bachelor thesis on transformer compression' all describe the SAME
project but share zero substring. Same for 'BERT' vs 'Transformer/BERT'
on skills, 'Marc' vs 'Marc Trujols' on contacts.

Before inserting a new entity, we ask Haiku: 'Is this a duplicate of
any of the existing ones? Output NEW or MATCH:<id>'. Captures synonyms,
abbreviations, translations, partial naming. ~$0.0002 per upsert.

This module exposes ``find_duplicate(llm, kind, new_data, existing) -> int | None``.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from src.brain.llm_client import LLMClient


logger = logging.getLogger(__name__)


# Per-entity formatter for the existing-list context. Keep them concise —
# Haiku only needs enough to match identity, not full records.
_EXISTING_FORMATTERS = {
    "project": lambda r: (
        f"#{r.id} '{r.name}' — {(r.description or '')[:140]}"
        + (f" [{r.status}]" if getattr(r, "status", None) else "")
    ),
    "work_experience": lambda r: (
        f"#{r.id} {r.role or '?'} @ {r.company or '?'} "
        f"({r.start_date or '?'} → {r.end_date or 'present'})"
    ),
    "education": lambda r: (
        f"#{r.id} {r.degree or 'studies'} @ {r.institution or '?'}"
    ),
    "skill": lambda r: f"#{r.id} '{r.name}'" + (f" ({r.category})" if getattr(r, "category", None) else ""),
    "contact": lambda r: (
        f"#{r.id} {r.name}"
        + (f" — {r.organization}" if getattr(r, "organization", None) else "")
        + (f" — {r.relationship}" if getattr(r, "relationship", None) else "")
    ),
    "idea": lambda r: f"#{r.id} {(r.content or '')[:160]}",
}


def _format_new(kind: str, data: dict[str, Any]) -> str:
    if kind == "project":
        return (
            f"name='{data.get('name')}' · "
            f"description='{(data.get('description') or '')[:200]}'"
        )
    if kind == "work_experience":
        return f"role='{data.get('role')}' @ company='{data.get('company')}'"
    if kind == "education":
        return f"degree='{data.get('degree')}' @ institution='{data.get('institution')}'"
    if kind == "skill":
        return f"name='{data.get('name')}' · category='{data.get('category') or '-'}'"
    if kind == "contact":
        return (
            f"name='{data.get('name')}' · "
            f"organization='{data.get('organization') or '-'}' · "
            f"relationship='{data.get('relationship') or '-'}'"
        )
    if kind == "idea":
        return f"content='{(data.get('content') or '')[:200]}'"
    return json.dumps(data, ensure_ascii=False, default=str)[:300]


PROMPT = """You're a duplicate-detection helper for a personal memory system.

The user is adding a NEW {kind} entity. Existing {kind}s on file:

{existing_block}

NEW entity to add:
{new_block}

Decide: is the new entity essentially the SAME as one of the existing
ones (same person/project/skill/etc., even if phrased differently —
synonyms, translations, abbreviations, partial naming all count as
the same)? If yes, the new entity should UPDATE the existing row, not
create a duplicate.

Output exactly ONE line:
- NEW                — distinct entity, insert as new
- MATCH:<id>         — same entity as existing #<id>, merge into it

No prose, no explanation. Just NEW or MATCH:<id>.
"""


async def find_duplicate(
    llm: LLMClient,
    kind: str,
    new_data: dict[str, Any],
    existing: list[Any],
) -> int | None:
    """Returns the id of the existing entity if the new data is a
    semantic duplicate, else None. Falls back to None on any error
    (better to let caller insert a new row than to bail out)."""
    if not existing:
        return None
    fmt = _EXISTING_FORMATTERS.get(kind)
    if fmt is None:
        return None
    try:
        existing_block = "\n".join(fmt(r) for r in existing[:30])
    except Exception as exc:
        logger.debug("dedup format failed for %s: %s", kind, exc)
        return None
    new_block = _format_new(kind, new_data)
    try:
        verdict = await llm.complete(
            prompt=PROMPT.format(
                kind=kind, existing_block=existing_block, new_block=new_block,
            ),
            system="You output exactly one line: NEW or MATCH:<id>.",
            model_tier="tiny",
            max_tokens=20,
            temperature=0.0,
        )
    except Exception as exc:
        logger.debug("dedup LLM call failed for %s: %s", kind, exc)
        return None
    line = (verdict or "").strip().splitlines()[0].strip().upper() if verdict else ""
    if line.startswith("MATCH:"):
        digits = "".join(c for c in line[6:] if c.isdigit())
        if digits:
            return int(digits)
    return None
