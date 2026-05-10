"""Career skills: onboarding, prep, tracker, variants, gap."""
from __future__ import annotations

import pytest

from src.brain.context_builder import Context
from src.memory.db import session_scope
from src.memory.structured import StructuredStore
from src.skills.career_onboarding import CareerOnboardingSkill, QUESTION_BANK
from src.skills.cv_variants import CVVariantsSkill, SkillGapSkill
from src.skills.interview_prep import InterviewPrepSkill
from src.skills.job_tracker import JobTrackerSkill


def _ctx(user_id: int = 1, profile: dict | None = None) -> Context:
    return Context(user_id=user_id, profile=profile or {"name": "Guido"})


@pytest.mark.asyncio
async def test_onboarding_starts_first_question(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=1)
    skill = CareerOnboardingSkill(fake_llm, memory_manager)
    sr = await skill.execute("/onboard", _ctx(user_id))
    assert "Q1/" in sr.text
    assert sr.metadata["onboarding"] is True


@pytest.mark.asyncio
async def test_onboarding_advances_question(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=2)
    skill = CareerOnboardingSkill(fake_llm, memory_manager)
    ctx = _ctx(user_id)
    await skill.execute("/onboard", ctx)
    sr = await skill.execute("Guido, based in Barcelona", ctx)
    assert "Q2/" in sr.text


@pytest.mark.asyncio
async def test_onboarding_done_finalises(fake_llm, memory_manager):
    import json

    fake_llm.responses["return only valid json"] = json.dumps({
        "projects": [], "work_experience": [], "education": [], "skills": [],
        "contacts": [], "achievements": [], "life_events": [], "ideas": [],
        "books": [], "decisions": [], "health_events": [], "quotes": [],
        "job_opportunities": [],
        "user_profile_updates": {"name": "Guido"},
    })
    user_id = memory_manager.ensure_user(telegram_id=3)
    skill = CareerOnboardingSkill(fake_llm, memory_manager)
    ctx = _ctx(user_id)
    await skill.execute("/onboard", ctx)
    await skill.execute("Guido, based in Barcelona", ctx)
    sr = await skill.execute("/done", ctx)
    assert "Onboarding" in sr.text or "complete" in sr.text.lower()
    assert sr.metadata.get("onboarding_done") is True


@pytest.mark.asyncio
async def test_interview_prep_uses_powerful_model(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=4)
    fake_llm.default = "## STAR answers\n..."
    skill = InterviewPrepSkill(fake_llm, memory_manager)
    sr = await skill.execute("/prep Staff ML Engineer at Meta", _ctx(user_id))
    assert "STAR" in sr.text
    assert any(c["tier"] == "powerful" for c in fake_llm.calls)


@pytest.mark.asyncio
async def test_job_tracker_lists_empty(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=5)
    skill = JobTrackerSkill(fake_llm, memory_manager)
    sr = await skill.execute("/jobs", _ctx(user_id))
    assert "No job" in sr.text


@pytest.mark.asyncio
async def test_job_tracker_set_and_list(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=6)
    skill = JobTrackerSkill(fake_llm, memory_manager)
    await skill.execute("/jobs Acme | Staff Engineer | applied", _ctx(user_id))
    sr = await skill.execute("/jobs", _ctx(user_id))
    assert "Acme" in sr.text and "applied" in sr.text


@pytest.mark.asyncio
async def test_cv_variants_persists_two(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=7)
    fake_llm.default = "# CV body\n## Summary\n..."
    skill = CVVariantsSkill(fake_llm, memory_manager)
    sr = await skill.execute("/cv_variants Senior backend at Stripe", _ctx(user_id))
    assert sr.metadata["variant_ids"] and len(sr.metadata["variant_ids"]) == 2
    with session_scope() as s:
        variants = StructuredStore(s).list_cv_variants(user_id)
        assert len(variants) == 2


@pytest.mark.asyncio
async def test_skill_gap_returns_analysis(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=8)
    fake_llm.default = "1. Strong matches: ..."
    skill = SkillGapSkill(fake_llm, memory_manager)
    sr = await skill.execute(
        "/gap Looking for a Rust SRE role at Cloudflare",
        _ctx(user_id, profile={"name": "Guido", "skills": [{"name": "Python"}]}),
    )
    assert "Strong matches" in sr.text
