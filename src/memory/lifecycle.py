"""Memory lifecycle: importance scoring, semantic dedup, forgetting curve."""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

from sqlalchemy import update

from src.memory import models as m
from src.memory.db import session_scope
from src.memory.semantic import SemanticStore


logger = logging.getLogger(__name__)


def importance_score(text: str, hint: dict[str, Any] | None = None) -> float:
    """Simple heuristic 0..1 score used when the LLM doesn't provide one.

    Length, presence of dates / numbers / proper nouns are positive signals.
    Easily replaced with a learned model later.
    """
    if not text:
        return 0.3
    score = 0.4
    score += min(0.2, len(text) / 1500)
    if any(c.isdigit() for c in text):
        score += 0.1
    if any(w[:1].isupper() for w in text.split()):
        score += 0.1
    if hint:
        category = (hint.get("category") or "").lower()
        if category in ("achievement", "project", "life_event"):
            score += 0.15
        if category == "idea":
            score += 0.05
    return max(0.05, min(1.0, score))


def decay_score(importance: float, days_since_reference: float, half_life_days: float = 60.0) -> float:
    """Exponential decay applied during nightly maintenance."""
    if importance <= 0:
        return 0.0
    decayed = importance * math.exp(-math.log(2) * days_since_reference / half_life_days)
    return max(0.0, min(1.0, decayed))


class LifecycleManager:
    """Coordinates dedup at write time and decay/prune as a maintenance job."""

    def __init__(
        self,
        semantic: SemanticStore,
        dedup_distance: float = 0.12,
        prune_below: float = 0.08,
        half_life_days: float = 60.0,
    ) -> None:
        self.semantic = semantic
        self.dedup_distance = dedup_distance
        self.prune_below = prune_below
        self.half_life_days = half_life_days

    # --- Dedup -----------------------------------------------------------
    def add_with_dedup(
        self,
        collection: str,
        text: str,
        metadata: dict[str, Any],
    ) -> tuple[str, bool]:
        """Add to the semantic store unless a near-duplicate exists.

        Returns (id, deduplicated). When deduplicated, the existing entry's
        last_referenced_at metadata is bumped instead of inserting.
        """
        try:
            user_id = metadata.get("user_id")
            where = {"user_id": user_id} if user_id is not None else None
            hits = self.semantic.search(collection, text, n_results=1, where=where)
        except Exception as exc:
            logger.warning("Dedup search failed: %s", exc)
            hits = []

        if hits and hits[0].distance <= self.dedup_distance:
            return hits[0].id, True

        doc_id = self.semantic.add(collection=collection, text=text, metadata=metadata)
        return doc_id, False

    # --- Maintenance -----------------------------------------------------
    def run_maintenance(self, user_id: int) -> dict[str, int]:
        """Decay structured importance fields and prune below threshold."""
        now = datetime.utcnow()
        decayed = 0
        pruned: list[tuple[str, int]] = []
        with session_scope() as s:
            for cls, label in ((m.Project, "project"), (m.Idea, "idea")):
                rows = s.query(cls).filter(cls.user_id == user_id).all()
                for row in rows:
                    ref = row.last_referenced_at or row.updated_at or row.created_at
                    days = max(0.0, (now - ref).total_seconds() / 86400.0)
                    new_imp = decay_score(row.importance or 0.5, days, self.half_life_days)
                    if abs(new_imp - (row.importance or 0.5)) > 0.001:
                        row.importance = new_imp
                        decayed += 1
                    if new_imp < self.prune_below and row.status in ("abandoned", "archived"):
                        s.delete(row)
                        pruned.append((label, row.id))
                        s.add(
                            m.AuditLog(
                                user_id=user_id,
                                action="prune",
                                entity_type=label,
                                entity_id=row.id,
                                details={"importance": new_imp},
                            )
                        )
        return {"decayed": decayed, "pruned": len(pruned)}

    def bump_reference(self, entity_type: str, entity_id: int) -> None:
        """Mark an entity as just-referenced so decay holds off."""
        with session_scope() as s:
            cls = {"project": m.Project, "idea": m.Idea}.get(entity_type)
            if cls is None:
                return
            s.execute(
                update(cls)
                .where(cls.id == entity_id)
                .values(last_referenced_at=datetime.utcnow())
            )
