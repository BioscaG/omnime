"""Memory manager — coordinator across structured + semantic stores."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from src.brain.llm_client import LLMClient
from src.memory.db import session_scope
from src.memory.extractor import EntityExtractor, Extraction
from src.memory.semantic import SemanticHit, SemanticStore
from src.memory.structured import StructuredStore
from src.memory.summarizer import Summarizer


logger = logging.getLogger(__name__)


@dataclass
class StoreResult:
    extraction: Extraction
    stored_summary: str


class MemoryManager:
    def __init__(
        self,
        llm: LLMClient,
        semantic: Optional[SemanticStore] = None,
    ) -> None:
        self.llm = llm
        self.semantic = semantic or SemanticStore()
        self.extractor = EntityExtractor(llm)
        self.summarizer = Summarizer(llm)

    # --- User -----------------------------------------------------------
    def ensure_user(self, telegram_id: int, name: str | None = None) -> int:
        with session_scope() as s:
            store = StructuredStore(s)
            user = store.get_or_create_user(telegram_id=telegram_id, name=name)
            return user.id

    def get_user_profile(self, user_id: int) -> dict[str, Any]:
        with session_scope() as s:
            store = StructuredStore(s)
            from src.memory import models as m

            user = s.get(m.UserProfile, user_id)
            if not user:
                return {}
            return {
                "id": user.id,
                "name": user.name,
                "bio": user.bio,
                "living_profile": user.living_profile,
                "communication_style": user.communication_style,
                "personality_traits": user.personality_traits,
                "preferences": user.preferences,
                "projects": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "status": p.status,
                        "role": p.role,
                        "description": p.description,
                        "technologies": p.technologies,
                    }
                    for p in store.list_projects(user_id)
                ],
                "work_experience": [
                    {
                        "id": w.id,
                        "company": w.company,
                        "role": w.role,
                        "start_date": str(w.start_date) if w.start_date else None,
                        "end_date": str(w.end_date) if w.end_date else None,
                    }
                    for w in store.list_work_experience(user_id)
                ],
                "education": [
                    {
                        "institution": e.institution,
                        "degree": e.degree,
                        "field": e.field,
                    }
                    for e in store.list_education(user_id)
                ],
                "skills": [
                    {"name": s.name, "proficiency": s.proficiency, "category": s.category}
                    for s in store.list_skills(user_id)
                ],
                "contacts_count": len(store.list_contacts(user_id)),
            }

    # --- Storage --------------------------------------------------------
    async def process_and_store(
        self,
        user_id: int,
        message: str,
        context_hint: str = "",
    ) -> StoreResult:
        extraction = await self.extractor.extract(message, context=context_hint)

        with session_scope() as s:
            store = StructuredStore(s)
            for proj in extraction.projects:
                store.upsert_project(user_id=user_id, **proj)
            for we in extraction.work_experience:
                store.upsert_work_experience(user_id=user_id, **we)
            for ed in extraction.education:
                store.upsert_education(user_id=user_id, **ed)
            for sk in extraction.skills:
                store.upsert_skill(user_id=user_id, **sk)
            for c in extraction.contacts:
                store.upsert_contact(user_id=user_id, **c)
            for ach in extraction.achievements:
                store.add_achievement(user_id=user_id, **ach)
            for ev in extraction.life_events:
                store.add_life_event(user_id=user_id, **ev)
            for idea in extraction.ideas:
                store.add_idea(user_id=user_id, **idea)
            if extraction.user_profile_updates:
                upd = extraction.user_profile_updates
                fields: dict[str, Any] = {}
                if upd.get("name"):
                    fields["name"] = upd["name"]
                if upd.get("communication_style"):
                    fields["communication_style"] = upd["communication_style"]
                if upd.get("personality_traits"):
                    fields["personality_traits"] = upd["personality_traits"]
                if upd.get("preferences"):
                    fields["preferences"] = upd["preferences"]
                if fields:
                    store.update_user(user_id, **fields)

        # Semantic indexing — store the raw message + structured highlights
        self.semantic.add(
            collection="conversations",
            text=message,
            metadata={
                "user_id": user_id,
                "date": datetime.utcnow().isoformat(),
                "source": "conversation",
            },
        )
        for proj in extraction.projects:
            if proj.get("name"):
                self.semantic.add(
                    collection="knowledge",
                    text=f"Project '{proj['name']}': {proj.get('description') or ''} "
                    f"Tech: {', '.join(proj.get('technologies') or [])}",
                    metadata={
                        "user_id": user_id,
                        "category": "project",
                        "name": proj["name"],
                    },
                )
        for ach in extraction.achievements:
            if ach.get("title"):
                self.semantic.add(
                    collection="knowledge",
                    text=f"Achievement: {ach['title']}. {ach.get('description') or ''}",
                    metadata={"user_id": user_id, "category": "achievement"},
                )
        for ev in extraction.life_events:
            if ev.get("title"):
                self.semantic.add(
                    collection="knowledge",
                    text=f"Life event: {ev['title']}. {ev.get('description') or ''}",
                    metadata={"user_id": user_id, "category": "life_event"},
                )
        for idea in extraction.ideas:
            if idea.get("content"):
                self.semantic.add(
                    collection="knowledge",
                    text=f"Idea: {idea['content']}",
                    metadata={"user_id": user_id, "category": "idea"},
                )

        return StoreResult(extraction=extraction, stored_summary=extraction.summary())

    # --- Retrieval ------------------------------------------------------
    def semantic_search(
        self,
        user_id: int,
        query: str,
        n_results: int = 8,
    ) -> list[SemanticHit]:
        hits: list[SemanticHit] = []
        for collection in ("knowledge", "conversations", "documents"):
            try:
                hits.extend(
                    self.semantic.search(
                        collection=collection,
                        query=query,
                        n_results=n_results,
                        where={"user_id": user_id},
                    )
                )
            except Exception as exc:
                logger.warning("Semantic search error in %s: %s", collection, exc)
        hits.sort(key=lambda h: h.distance)
        return hits[:n_results]

    def log_message(
        self,
        user_id: int,
        text: str,
        role: str,
        intent: str | None = None,
        entities: dict[str, Any] | None = None,
        telegram_message_id: int | None = None,
    ) -> None:
        with session_scope() as s:
            StructuredStore(s).log_message(
                user_id=user_id,
                message_text=text,
                role=role,
                intent=intent,
                entities_extracted=entities,
                telegram_message_id=telegram_message_id,
            )

    def recent_messages(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        with session_scope() as s:
            rows = StructuredStore(s).recent_messages(user_id, limit=limit)
            return [
                {
                    "role": r.role,
                    "text": r.message_text,
                    "intent": r.intent,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
