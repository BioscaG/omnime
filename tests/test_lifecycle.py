"""Importance scoring, decay and semantic dedup."""
from __future__ import annotations

import pytest

from src.memory.lifecycle import LifecycleManager, decay_score, importance_score
from src.memory.semantic import SemanticStore


def test_importance_score_in_range():
    s = importance_score("Short")
    assert 0.0 <= s <= 1.0
    long_s = importance_score(
        "I just shipped the new pricing model that increased revenue by 40%.",
        hint={"category": "achievement"},
    )
    assert long_s > s


def test_decay_halves_at_half_life():
    decayed = decay_score(1.0, days_since_reference=60.0, half_life_days=60.0)
    assert pytest.approx(decayed, rel=0.05) == 0.5


def test_dedup_bumps_existing_close_match():
    semantic = SemanticStore(in_memory=True)
    # Threshold large enough to fire on identical text regardless of which
    # embedding model is loaded by chroma in the host process.
    lifecycle = LifecycleManager(semantic, dedup_distance=100.0)

    lifecycle.add_with_dedup("knowledge", "Met Sarah Chen at Google", {"user_id": 1})
    _id, dup = lifecycle.add_with_dedup(
        "knowledge", "Met Sarah Chen at Google", {"user_id": 1}
    )
    assert dup is True
    assert semantic.count("knowledge") == 1


def test_dedup_inserts_when_distinct():
    semantic = SemanticStore(in_memory=True)
    # Very tight threshold so unrelated entries are always inserted.
    lifecycle = LifecycleManager(semantic, dedup_distance=0.001)

    lifecycle.add_with_dedup("knowledge", "Project ATLAS uses Python", {"user_id": 1})
    initial = semantic.count("knowledge")
    _id, dup = lifecycle.add_with_dedup(
        "knowledge", "Trip to Tokyo last summer", {"user_id": 1}
    )
    assert dup is False
    assert semantic.count("knowledge") == initial + 1
