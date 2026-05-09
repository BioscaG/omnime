"""Skill registry + individual skills."""
from __future__ import annotations

import pytest

from src.skills.cv_generator import CVGeneratorSkill
from src.skills.email_composer import EmailComposerSkill
from src.skills.code_generator import CodeGeneratorSkill
from src.skills.registry import SkillRegistry


@pytest.fixture
def registry(fake_llm, memory_manager):
    return SkillRegistry(llm=fake_llm, memory=memory_manager)


def test_can_handle_scoring(fake_llm, memory_manager):
    cv = CVGeneratorSkill(fake_llm, memory_manager)
    assert cv.can_handle("/cv") >= 0.9
    assert cv.can_handle("can you generate my CV?") >= 0.7
    assert cv.can_handle("hello there") < 0.4


def test_email_skill_triggers(fake_llm, memory_manager):
    skill = EmailComposerSkill(fake_llm, memory_manager)
    assert skill.can_handle("/email Sarah about the meeting") >= 0.9
    assert skill.can_handle("draft an email to John") >= 0.8


def test_registry_find_best(registry, fake_llm, memory_manager):
    registry.register(CVGeneratorSkill(fake_llm, memory_manager))
    registry.register(EmailComposerSkill(fake_llm, memory_manager))
    registry.register(CodeGeneratorSkill(fake_llm, memory_manager))

    class FakeContext:
        intent = "TASK"

    chosen = registry.find_best_skill("/cv", FakeContext())
    assert chosen is not None and chosen.name == "cv_generator"

    chosen = registry.find_best_skill("draft an email", FakeContext())
    assert chosen is not None and chosen.name == "email_composer"

    chosen = registry.find_best_skill("/code write a fizzbuzz", FakeContext())
    assert chosen is not None and chosen.name == "code_generator"
