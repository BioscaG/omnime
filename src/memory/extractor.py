"""Entity extraction from natural-language messages.

Uses an LLM in JSON mode to detect projects, contacts, skills, achievements,
life events, ideas, work experience and education from free text. The output
is normalised so the memory manager can persist it.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from src.brain.llm_client import LLMClient


logger = logging.getLogger(__name__)


EXTRACTION_PROMPT = """You are an information extractor for a personal AI assistant.
Your job: read the user's message and extract structured entities to remember.

Today's date: {today}

Existing context (use to avoid duplicates and merge updates):
{context}

Output ONLY valid JSON (no preamble) matching this schema:
{{
  "projects": [{{"name": str, "description": str|null, "role": str|null,
                 "technologies": [str], "status": str|null,
                 "start_date": "YYYY-MM-DD"|null, "end_date": "YYYY-MM-DD"|null,
                 "key_achievements": [str], "details": str|null}}],
  "work_experience": [{{"company": str, "role": str, "description": str|null,
                        "start_date": "YYYY-MM-DD"|null, "end_date": "YYYY-MM-DD"|null,
                        "achievements": [str], "technologies": [str],
                        "location": str|null, "remote": bool|null}}],
  "education": [{{"institution": str, "degree": str|null, "field": str|null,
                  "start_date": "YYYY-MM-DD"|null, "end_date": "YYYY-MM-DD"|null,
                  "grade": str|null, "thesis": str|null}}],
  "skills": [{{"name": str, "category": str|null,
               "proficiency": str|null, "years_experience": float|null,
               "context": str|null}}],
  "contacts": [{{"name": str, "relationship": str|null, "organization": str|null,
                 "email": str|null, "phone": str|null, "notes": str|null,
                 "context": str|null}}],
  "achievements": [{{"title": str, "description": str|null,
                     "date": "YYYY-MM-DD"|null, "category": str|null,
                     "impact": str|null}}],
  "life_events": [{{"title": str, "description": str|null,
                    "date": "YYYY-MM-DD"|null, "category": str|null,
                    "location": str|null, "people_involved": [str],
                    "lessons_learned": str|null}}],
  "ideas": [{{"content": str, "category": str|null, "tags": [str]}}],
  "user_profile_updates": {{"name": str|null, "bio_addition": str|null,
                            "communication_style": str|null,
                            "personality_traits": object|null,
                            "preferences": object|null}}
}}

Rules:
- Only extract entities that are clearly present.
- Resolve relative dates ("yesterday", "last week", "next Monday") using today's date.
- Use ISO date format YYYY-MM-DD.
- Empty arrays / null when nothing applies.
- Do NOT invent details that aren't in the message or context.

User message:
\"\"\"
{message}
\"\"\"
"""


@dataclass
class Extraction:
    projects: list[dict[str, Any]] = field(default_factory=list)
    work_experience: list[dict[str, Any]] = field(default_factory=list)
    education: list[dict[str, Any]] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)
    contacts: list[dict[str, Any]] = field(default_factory=list)
    achievements: list[dict[str, Any]] = field(default_factory=list)
    life_events: list[dict[str, Any]] = field(default_factory=list)
    ideas: list[dict[str, Any]] = field(default_factory=list)
    user_profile_updates: dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any(
            [
                self.projects,
                self.work_experience,
                self.education,
                self.skills,
                self.contacts,
                self.achievements,
                self.life_events,
                self.ideas,
                self.user_profile_updates,
            ]
        )

    def summary(self) -> str:
        parts: list[str] = []
        if self.projects:
            parts.append(f"{len(self.projects)} project(s)")
        if self.work_experience:
            parts.append(f"{len(self.work_experience)} job(s)")
        if self.education:
            parts.append(f"{len(self.education)} education entry/ies")
        if self.skills:
            parts.append(f"{len(self.skills)} skill(s)")
        if self.contacts:
            parts.append(f"{len(self.contacts)} contact(s)")
        if self.achievements:
            parts.append(f"{len(self.achievements)} achievement(s)")
        if self.life_events:
            parts.append(f"{len(self.life_events)} life event(s)")
        if self.ideas:
            parts.append(f"{len(self.ideas)} idea(s)")
        if self.user_profile_updates:
            parts.append("profile update")
        return ", ".join(parts) if parts else "nothing new"


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
    return m.group(1) if m else s


class EntityExtractor:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def extract(
        self,
        message: str,
        context: str = "",
        today: date | None = None,
    ) -> Extraction:
        today = today or date.today()
        prompt = EXTRACTION_PROMPT.format(
            today=today.isoformat(),
            context=context or "(no context)",
            message=message,
        )
        try:
            raw = await self.llm.complete(
                prompt=prompt,
                system="You return only valid JSON, nothing else.",
                model_tier="fast",
                max_tokens=2000,
            )
        except Exception as exc:
            logger.warning("LLM extraction failed: %s", exc)
            return Extraction()

        try:
            data = json.loads(_strip_code_fence(raw))
        except json.JSONDecodeError as exc:
            logger.warning("Could not parse extraction JSON: %s\nRaw: %s", exc, raw[:500])
            return Extraction()

        return Extraction(
            projects=data.get("projects") or [],
            work_experience=data.get("work_experience") or [],
            education=data.get("education") or [],
            skills=data.get("skills") or [],
            contacts=data.get("contacts") or [],
            achievements=data.get("achievements") or [],
            life_events=data.get("life_events") or [],
            ideas=data.get("ideas") or [],
            user_profile_updates=data.get("user_profile_updates") or {},
        )
