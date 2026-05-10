"""EvolutionEngine: AST + sandbox + LLM review gate the deployment."""
from __future__ import annotations

import json
import textwrap

import pytest

from src.evolution.self_coder import EvolutionEngine
from src.skills.registry import SkillRegistry


GOOD_CODE = textwrap.dedent("""
    from src.skills.base import BaseSkill, SkillResponse


    class HelloSkill(BaseSkill):
        name = "hello"
        description = "say hi"
        triggers = ["hi"]

        def __init__(self, llm, memory):
            self.llm = llm
            self.memory = memory

        def can_handle(self, message, intent=None):
            return 0.5

        async def execute(self, message, context):
            return SkillResponse(text="hi")
""")


class _StubContext:
    def __init__(self) -> None:
        self.user_id = 1
        self.intent = None

    def to_prompt_block(self) -> str:
        return ""


@pytest.mark.asyncio
async def test_engine_rejects_when_review_disapproves(fake_llm, memory_manager):
    fake_llm.responses["you write python skill modules"] = GOOD_CODE
    fake_llm.responses["careful security-minded"] = json.dumps({
        "approved": False, "issues": ["uses too much"], "summary": "no"
    })
    registry = SkillRegistry(llm=fake_llm, memory=memory_manager)
    engine = EvolutionEngine(llm=fake_llm, memory=memory_manager, registry=registry)
    ctx = _StubContext()

    response = await engine.handle(user_id=1, message="add hello skill", context=ctx)
    assert response.intent.value == "EVOLVE"
    assert "Code review flagged" in response.text


@pytest.mark.asyncio
async def test_engine_rejects_bad_ast(fake_llm, memory_manager):
    fake_llm.responses["you write python skill modules"] = "import os\nclass X: pass\n"
    registry = SkillRegistry(llm=fake_llm, memory=memory_manager)
    engine = EvolutionEngine(llm=fake_llm, memory=memory_manager, registry=registry)

    response = await engine.handle(user_id=1, message="evolve", context=_StubContext())
    assert "smoke test failed" in response.text


@pytest.mark.asyncio
async def test_engine_pending_when_approved(fake_llm, memory_manager):
    fake_llm.responses["you write python skill modules"] = GOOD_CODE
    fake_llm.responses["careful security-minded"] = json.dumps({
        "approved": True, "issues": [], "summary": "ok"
    })
    registry = SkillRegistry(llm=fake_llm, memory=memory_manager)
    engine = EvolutionEngine(llm=fake_llm, memory=memory_manager, registry=registry)

    response = await engine.handle(user_id=1, message="add hello", context=_StubContext())
    assert response.intent.value == "EVOLVE"
    assert "drafted a new skill" in response.text
    assert 1 in engine._pending
