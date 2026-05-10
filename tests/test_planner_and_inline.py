"""Agentic planner + inline mode helpers."""
from __future__ import annotations

import json

import pytest

from src.brain.context_builder import ContextBuilder
from src.brain.planner import Planner
from src.skills.registry import SkillRegistry


@pytest.mark.asyncio
async def test_planner_executes_steps_in_order(fake_llm, memory_manager):
    fake_llm.responses["produce concise json plans"] = json.dumps({
        "steps": [
            {"skill": "knowledge_graph", "input": "/graph", "why": "context"},
            {"skill": "reading_list", "input": "/books", "why": "show shelf"},
        ],
        "final": "Compact overview returned."
    })
    user_id = memory_manager.ensure_user(telegram_id=1)
    registry = SkillRegistry(llm=fake_llm, memory=memory_manager)

    from src.skills.knowledge_graph import KnowledgeGraphSkill
    from src.skills.reading_list import ReadingListSkill

    registry.register(KnowledgeGraphSkill(fake_llm, memory_manager))
    registry.register(ReadingListSkill(fake_llm, memory_manager))

    planner = Planner(llm=fake_llm, registry=registry, builder=ContextBuilder(memory_manager))
    result = await planner.run(user_id, "Show me my world", max_steps=2)
    assert len(result.steps) == 2
    assert result.steps[0].skill == "knowledge_graph"


@pytest.mark.asyncio
async def test_planner_returns_empty_when_plan_invalid(fake_llm, memory_manager):
    fake_llm.responses["produce concise json plans"] = "not json"
    user_id = memory_manager.ensure_user(telegram_id=2)
    registry = SkillRegistry(llm=fake_llm, memory=memory_manager)
    planner = Planner(llm=fake_llm, registry=registry, builder=ContextBuilder(memory_manager))
    result = await planner.run(user_id, "do stuff")
    assert "Couldn't draft" in result.final_text
