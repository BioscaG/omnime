"""Memory manager — coordinator across structured + semantic stores."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import select

from src.brain.llm_client import LLMClient
from src.memory import models as m
from src.memory.db import session_scope
from src.memory.extractor import EntityExtractor, Extraction
from src.memory.lifecycle import LifecycleManager, importance_score
from src.memory.semantic import SemanticHit, SemanticStore
from src.memory.structured import StructuredStore
from src.memory.summarizer import Summarizer


logger = logging.getLogger(__name__)


@dataclass
class StoreResult:
    extraction: Extraction
    stored_summary: str
    deduplicated: int = 0


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
        self.lifecycle = LifecycleManager(self.semantic)

    # --- User -----------------------------------------------------------
    def ensure_user(self, telegram_id: int, name: str | None = None) -> int:
        with session_scope() as s:
            store = StructuredStore(s)
            user = store.get_or_create_user(telegram_id=telegram_id, name=name)
            return user.id

    def get_user_profile(self, user_id: int) -> dict[str, Any]:
        with session_scope() as s:
            store = StructuredStore(s)
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
                        "importance": p.importance,
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
                    {"name": sk.name, "proficiency": sk.proficiency, "category": sk.category}
                    for sk in store.list_skills(user_id)
                ],
                "contacts_count": len(store.list_contacts(user_id)),
                "active_goals": [
                    {
                        "id": g.id,
                        "description": g.description,
                        "streak": g.streak,
                        "cadence": g.cadence,
                    }
                    for g in store.list_goals(user_id, status="active")
                ],
            }

    # --- Storage --------------------------------------------------------
    async def process_and_store(
        self,
        user_id: int,
        message: str,
        context_hint: str = "",
    ) -> StoreResult:
        extraction = await self.extractor.extract(message, context=context_hint)

        # LLM-based dedup pre-checks. Done once per kind so we can pass
        # the existing list to Haiku for semantic matching ('TFG SVD' ==
        # 'Anatomía Emocional de BERT'). Falls back to None on any error,
        # which means upsert uses its exact-match logic — safe degrade.
        from src.memory.dedup import find_duplicate

        async def _resolve_match(kind: str, new: dict, existing: list) -> int | None:
            try:
                return await find_duplicate(self.llm, kind, new, existing)
            except Exception as exc:
                logger.debug("dedup %s failed: %s", kind, exc)
                return None

        with session_scope() as s:
            store = StructuredStore(s)
            existing_projects = store.list_projects(user_id)
            existing_work = store.list_work_experience(user_id)
            existing_edu = store.list_education(user_id)
            existing_skills = store.list_skills(user_id)
            existing_contacts = store.list_contacts(user_id)

            for proj in extraction.projects:
                mid = await _resolve_match("project", proj, existing_projects)
                store.upsert_project(user_id=user_id, match_id=mid, **proj)
            for we in extraction.work_experience:
                mid = await _resolve_match("work_experience", we, existing_work)
                store.upsert_work_experience(user_id=user_id, match_id=mid, **we)
            for ed in extraction.education:
                mid = await _resolve_match("education", ed, existing_edu)
                store.upsert_education(user_id=user_id, match_id=mid, **ed)
            for sk in extraction.skills:
                mid = await _resolve_match("skill", sk, existing_skills)
                store.upsert_skill(user_id=user_id, match_id=mid, **sk)
            for c in extraction.contacts:
                mid = await _resolve_match("contact", c, existing_contacts)
                store.upsert_contact(user_id=user_id, match_id=mid, **c)
            for ach in extraction.achievements:
                store.add_achievement(user_id=user_id, **ach)
            for ev in extraction.life_events:
                store.add_life_event(user_id=user_id, **ev)
            for idea in extraction.ideas:
                store.add_idea(user_id=user_id, **idea)
            for book in extraction.books:
                store.upsert_book(user_id=user_id, **book)
            for decision in extraction.decisions:
                store.upsert_decision(user_id=user_id, **decision)
            for hev in extraction.health_events:
                store.add_health_event(user_id=user_id, **hev)
            for q in extraction.quotes:
                store.add_quote(user_id=user_id, **q)
            for opp in extraction.job_opportunities:
                store.upsert_job_opportunity(user_id=user_id, **opp)
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

        deduplicated = 0
        try:
            _, dup = self.lifecycle.add_with_dedup(
                collection="conversations",
                text=message,
                metadata={
                    "user_id": user_id,
                    "date": datetime.utcnow().isoformat(),
                    "source": "conversation",
                    "importance": importance_score(message),
                },
            )
            deduplicated += int(dup)
        except Exception as exc:
            logger.warning("Conversation indexing failed: %s", exc)

        for proj in extraction.projects:
            if proj.get("name"):
                _, dup = self.lifecycle.add_with_dedup(
                    collection="knowledge",
                    text=f"Project '{proj['name']}': {proj.get('description') or ''} "
                    f"Tech: {', '.join(proj.get('technologies') or [])}",
                    metadata={
                        "user_id": user_id,
                        "category": "project",
                        "name": proj["name"],
                        "importance": importance_score(
                            proj.get("description") or "", {"category": "project"}
                        ),
                    },
                )
                deduplicated += int(dup)
        for ach in extraction.achievements:
            if ach.get("title"):
                _, dup = self.lifecycle.add_with_dedup(
                    collection="knowledge",
                    text=f"Achievement: {ach['title']}. {ach.get('description') or ''}",
                    metadata={
                        "user_id": user_id, "category": "achievement",
                        "importance": importance_score(
                            ach.get("description") or "", {"category": "achievement"}
                        ),
                    },
                )
                deduplicated += int(dup)
        for ev in extraction.life_events:
            if ev.get("title"):
                _, dup = self.lifecycle.add_with_dedup(
                    collection="knowledge",
                    text=f"Life event: {ev['title']}. {ev.get('description') or ''}",
                    metadata={
                        "user_id": user_id, "category": "life_event",
                        "importance": importance_score(
                            ev.get("description") or "", {"category": "life_event"}
                        ),
                    },
                )
                deduplicated += int(dup)
        for idea in extraction.ideas:
            if idea.get("content"):
                _, dup = self.lifecycle.add_with_dedup(
                    collection="knowledge",
                    text=f"Idea: {idea['content']}",
                    metadata={
                        "user_id": user_id, "category": "idea",
                        "importance": importance_score(
                            idea.get("content") or "", {"category": "idea"}
                        ),
                    },
                )
                deduplicated += int(dup)
        for book in extraction.books:
            if book.get("title"):
                _, dup = self.lifecycle.add_with_dedup(
                    collection="knowledge",
                    text=(
                        f"Book: {book['title']} by {book.get('author') or 'unknown'}. "
                        f"Takeaways: {', '.join(book.get('takeaways') or [])}"
                    ),
                    metadata={
                        "user_id": user_id, "category": "book",
                        "importance": importance_score(
                            " ".join(book.get("takeaways") or []) or book.get("title") or "",
                            {"category": "achievement"},
                        ),
                    },
                )
                deduplicated += int(dup)
        for d in extraction.decisions:
            if d.get("title"):
                _, dup = self.lifecycle.add_with_dedup(
                    collection="knowledge",
                    text=(
                        f"Decision: {d['title']}. Rationale: {d.get('rationale') or ''}. "
                        f"Outcome: {d.get('outcome') or 'pending'}"
                    ),
                    metadata={
                        "user_id": user_id, "category": "decision",
                        "importance": importance_score(
                            d.get("rationale") or d.get("title") or "",
                            {"category": "achievement"},
                        ),
                    },
                )
                deduplicated += int(dup)
        for h in extraction.health_events:
            if h.get("title"):
                _, dup = self.lifecycle.add_with_dedup(
                    collection="knowledge",
                    text=f"Health: {h['title']}. {h.get('description') or ''}",
                    metadata={
                        "user_id": user_id, "category": "health",
                        "importance": importance_score(h.get("description") or "", {"category": "life_event"}),
                    },
                )
                deduplicated += int(dup)
        for q in extraction.quotes:
            if q.get("text"):
                _, dup = self.lifecycle.add_with_dedup(
                    collection="knowledge",
                    text=f"Quote ({q.get('author') or q.get('source') or '—'}): {q['text']}",
                    metadata={"user_id": user_id, "category": "quote", "importance": 0.5},
                )
                deduplicated += int(dup)
        for opp in extraction.job_opportunities:
            if opp.get("company") and opp.get("role"):
                _, dup = self.lifecycle.add_with_dedup(
                    collection="knowledge",
                    text=(
                        f"Job opportunity: {opp['role']} @ {opp['company']}. "
                        f"Status: {opp.get('status') or 'discovered'}. "
                        f"{opp.get('description') or ''}"
                    ),
                    metadata={
                        "user_id": user_id, "category": "job_opportunity",
                        "importance": 0.7,
                    },
                )
                deduplicated += int(dup)

        try:
            self._maybe_register_goal_check_in(user_id, message)
        except Exception as exc:
            logger.warning("Goal check-in detection failed: %s", exc)

        return StoreResult(
            extraction=extraction,
            stored_summary=extraction.summary(),
            deduplicated=deduplicated,
        )

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

        def _score(h: SemanticHit) -> float:
            imp = float(h.metadata.get("importance", 0.5))
            return h.distance - 0.05 * imp

        hits.sort(key=_score)
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

    # --- Forget / audit -------------------------------------------------
    def forget(self, user_id: int, selector: str) -> str:
        sel = selector.strip()
        if not sel:
            return "Empty selector."
        if " " not in sel:
            return "Usage: /forget <type> <name>. Type ∈ project, contact, skill, idea, goal, memory."
        kind, _, query = sel.partition(" ")
        kind = kind.lower()
        query = query.strip()
        with session_scope() as s:
            store = StructuredStore(s)
            if kind == "project":
                rows = list(s.scalars(
                    select(m.Project).where(m.Project.user_id == user_id, m.Project.name.ilike(query))
                ))
                deleted = self._delete_rows(s, rows, user_id, "project")
            elif kind == "contact":
                rows = list(s.scalars(
                    select(m.Contact).where(m.Contact.user_id == user_id, m.Contact.name.ilike(query))
                ))
                deleted = self._delete_rows(s, rows, user_id, "contact")
            elif kind == "skill":
                rows = list(s.scalars(
                    select(m.Skill).where(m.Skill.user_id == user_id, m.Skill.name.ilike(query))
                ))
                deleted = self._delete_rows(s, rows, user_id, "skill")
            elif kind == "idea":
                rows = list(s.scalars(
                    select(m.Idea).where(m.Idea.user_id == user_id, m.Idea.content.ilike(f"%{query}%"))
                ))
                deleted = self._delete_rows(s, rows, user_id, "idea")
            elif kind == "goal":
                rows = list(s.scalars(
                    select(m.Goal).where(m.Goal.user_id == user_id, m.Goal.description.ilike(f"%{query}%"))
                ))
                deleted = self._delete_rows(s, rows, user_id, "goal")
            elif kind == "memory":
                self.semantic.delete("knowledge", query)
                self.semantic.delete("conversations", query)
                store.add_audit(user_id, "forget_memory", "memory", None, {"selector": query})
                return f"Forgot semantic entries matching id {query}."
            else:
                return f"Unknown selector type: {kind}"

        if not deleted:
            return f"No {kind} matched '{query}'."
        return f"Forgot {len(deleted)} {kind}(s): {', '.join(deleted)}"

    @staticmethod
    def _delete_rows(session, rows, user_id: int, entity_type: str) -> list[str]:
        deleted: list[str] = []
        for row in rows:
            label = getattr(row, "name", None) or getattr(row, "description", None) or str(row.id)
            session.add(
                m.AuditLog(
                    user_id=user_id,
                    action="forget",
                    entity_type=entity_type,
                    entity_id=row.id,
                    details={"label": label},
                )
            )
            session.delete(row)
            deleted.append(label)
        return deleted

    # --- Goals ----------------------------------------------------------
    def add_goal(self, user_id: int, description: str) -> dict[str, Any]:
        cadence = self._parse_cadence(description)
        with session_scope() as s:
            store = StructuredStore(s)
            g = store.add_goal(user_id=user_id, description=description, cadence=cadence)
            store.add_audit(user_id, "create", "goal", g.id, {"description": description})
            return {
                "id": g.id,
                "description": g.description,
                "streak": g.streak,
                "cadence": g.cadence,
            }

    def _maybe_register_goal_check_in(self, user_id: int, message: str) -> None:
        text = message.lower()
        with session_scope() as s:
            store = StructuredStore(s)
            today = date.today()
            for goal in store.list_goals(user_id, status="active"):
                key_terms = [w for w in goal.description.lower().split() if len(w) > 3]
                if not key_terms:
                    continue
                if all(term in text for term in key_terms[:2]):
                    if goal.last_check_in == today:
                        continue
                    if goal.last_check_in and (today - goal.last_check_in).days <= 1:
                        goal.streak = (goal.streak or 0) + 1
                    else:
                        goal.streak = 1
                    goal.longest_streak = max(goal.longest_streak or 0, goal.streak)
                    goal.last_check_in = today
                    store.add_audit(user_id, "check_in", "goal", goal.id, {"streak": goal.streak})

    @staticmethod
    def _parse_cadence(description: str) -> str | None:
        d = description.lower()
        if "daily" in d or "every day" in d:
            return "daily"
        if "weekly" in d or "every week" in d:
            return "weekly"
        if "month" in d:
            return "monthly"
        return None

    # --- Maintenance hook ----------------------------------------------
    def run_maintenance(self, user_id: int) -> dict[str, int]:
        return self.lifecycle.run_maintenance(user_id)
