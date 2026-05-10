"""Personal categories flow end-to-end through the extractor and stores."""
from __future__ import annotations

import json

import pytest

from src.memory.db import session_scope
from src.memory.structured import StructuredStore


@pytest.mark.asyncio
async def test_books_decisions_health_quotes_jobs_persist(fake_llm, memory_manager):
    fake_llm.responses["return only valid json"] = json.dumps({
        "projects": [], "work_experience": [], "education": [], "skills": [],
        "contacts": [], "achievements": [], "life_events": [], "ideas": [],
        "books": [{"title": "The Pragmatic Programmer", "author": "Hunt & Thomas",
                   "status": "finished", "rating": 4.5,
                   "takeaways": ["Use rubber-duck debugging"]}],
        "decisions": [{"title": "Take Acme offer", "rationale": "Better tech stack",
                       "alternatives": ["Stay at Globex"], "status": "made",
                       "decided_at": "2026-04-15"}],
        "health_events": [{"title": "ACL surgery", "category": "injury",
                           "date": "2026-03-12", "severity": "high"}],
        "quotes": [{"text": "Make it work, make it right, make it fast",
                    "author": "Kent Beck", "tags": ["engineering"]}],
        "job_opportunities": [{"company": "Acme", "role": "Staff Engineer",
                               "status": "applied", "applied_at": "2026-05-01"}],
        "user_profile_updates": {},
    })
    user_id = memory_manager.ensure_user(telegram_id=42)
    result = await memory_manager.process_and_store(user_id, "lots of stuff today")
    assert not result.extraction.is_empty()

    with session_scope() as s:
        store = StructuredStore(s)
        assert any(b.title == "The Pragmatic Programmer" for b in store.list_books(user_id))
        assert any(d.title == "Take Acme offer" for d in store.list_decisions(user_id))
        assert any(j.company == "Acme" for j in store.list_job_opportunities(user_id))


@pytest.mark.asyncio
async def test_extraction_is_empty_uses_new_fields(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=43)
    fake_llm.responses["return only valid json"] = json.dumps({
        "projects": [], "work_experience": [], "education": [], "skills": [],
        "contacts": [], "achievements": [], "life_events": [], "ideas": [],
        "books": [], "decisions": [],
        "health_events": [], "quotes": [],
        "job_opportunities": [{"company": "Acme", "role": "Engineer"}],
        "user_profile_updates": {},
    })
    result = await memory_manager.process_and_store(user_id, "applying soon")
    assert not result.extraction.is_empty()
    assert "1 job opportunity" in result.stored_summary
