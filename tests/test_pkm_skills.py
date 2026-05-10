"""PKM skills: knowledge graph, reading list, decision log."""
from __future__ import annotations

import pytest

from src.brain.context_builder import Context
from src.memory.db import session_scope
from src.memory.structured import StructuredStore
from src.skills.decision_log import DecisionLogSkill
from src.skills.knowledge_graph import KnowledgeGraphSkill
from src.skills.reading_list import ReadingListSkill


def _ctx(user_id: int) -> Context:
    return Context(user_id=user_id)


@pytest.mark.asyncio
async def test_knowledge_graph_renders_mermaid(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=1)
    with session_scope() as s:
        store = StructuredStore(s)
        store.upsert_project(
            user_id=user_id, name="OMNIME", status="active", technologies=["Python"]
        )
        store.upsert_skill(user_id=user_id, name="Python", proficiency="expert")
        store.upsert_contact(user_id=user_id, name="Sarah", organization="Globex")

    skill = KnowledgeGraphSkill(fake_llm, memory_manager)
    sr = await skill.execute("/graph", _ctx(user_id))
    assert "graph TD" in sr.text
    assert "OMNIME" in sr.text
    assert "Python" in sr.text


@pytest.mark.asyncio
async def test_reading_list_groups_by_status(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=2)
    with session_scope() as s:
        store = StructuredStore(s)
        store.upsert_book(user_id=user_id, title="Atomic Habits", status="finished",
                          rating=4.0, takeaways=["Identity-based change"])
        store.upsert_book(user_id=user_id, title="Designing Data-Intensive Applications",
                          status="reading")
    skill = ReadingListSkill(fake_llm, memory_manager)
    sr = await skill.execute("/books", _ctx(user_id))
    assert "Atomic Habits" in sr.text
    assert "Reading" in sr.text and "Finished" in sr.text


@pytest.mark.asyncio
async def test_decision_log_lists_then_recalls(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=3)
    with session_scope() as s:
        StructuredStore(s).upsert_decision(
            user_id=user_id, title="Quit consulting",
            rationale="Burnout", outcome="Better focus, lower income",
            status="made", decided_at="2025-09-01",
        )

    skill = DecisionLogSkill(fake_llm, memory_manager)
    sr = await skill.execute("/decisions", _ctx(user_id))
    assert "Quit consulting" in sr.text

    fake_llm.default = "Most similar past decision: Quit consulting."
    sr = await skill.execute("/decide should I quit my current job?", _ctx(user_id))
    assert "Quit consulting" in sr.text or "similar" in sr.text.lower()
