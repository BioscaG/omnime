"""Structured (PostgreSQL) CRUD operations for the memory system."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.memory import models as m


def _coerce_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


class StructuredStore:
    """Thin CRUD layer on top of SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # --- User profile -----------------------------------------------------
    def get_or_create_user(self, telegram_id: int, name: str | None = None) -> m.UserProfile:
        user = self.session.scalar(
            select(m.UserProfile).where(m.UserProfile.telegram_id == telegram_id)
        )
        if user:
            return user
        user = m.UserProfile(telegram_id=telegram_id, name=name)
        self.session.add(user)
        self.session.flush()
        return user

    def update_user(self, user_id: int, **fields: Any) -> m.UserProfile:
        user = self.session.get(m.UserProfile, user_id)
        if user is None:
            raise ValueError(f"User {user_id} not found")
        for k, v in fields.items():
            if hasattr(user, k):
                setattr(user, k, v)
        return user

    # --- Projects ---------------------------------------------------------
    def upsert_project(self, user_id: int, match_id: int | None = None, **data: Any) -> m.Project:
        existing = None
        if match_id is not None:
            existing = self.session.get(m.Project, match_id)
            if existing is not None and existing.user_id != user_id:
                existing = None  # security: don't merge across users
        if existing is None:
            name = data.get("name")
            if name:
                existing = self.session.scalar(
                    select(m.Project)
                    .where(m.Project.user_id == user_id)
                    .where(m.Project.name.ilike(name))
                )
        data["start_date"] = _coerce_date(data.get("start_date"))
        data["end_date"] = _coerce_date(data.get("end_date"))
        if existing:
            for k, v in data.items():
                if v is not None and hasattr(existing, k):
                    setattr(existing, k, v)
            return existing
        project = m.Project(user_id=user_id, **{k: v for k, v in data.items() if v is not None})
        self.session.add(project)
        self.session.flush()
        return project

    def list_projects(self, user_id: int, status: str | None = None) -> list[m.Project]:
        stmt = select(m.Project).where(m.Project.user_id == user_id)
        if status:
            stmt = stmt.where(m.Project.status == status)
        return list(self.session.scalars(stmt.order_by(m.Project.start_date.desc().nullslast())))

    # --- Work experience --------------------------------------------------
    def upsert_work_experience(self, user_id: int, match_id: int | None = None, **data: Any) -> m.WorkExperience:
        existing = None
        if match_id is not None:
            existing = self.session.get(m.WorkExperience, match_id)
            if existing is not None and existing.user_id != user_id:
                existing = None
        company = data.get("company")
        role = data.get("role")
        if existing is None and company and role:
            existing = self.session.scalar(
                select(m.WorkExperience)
                .where(m.WorkExperience.user_id == user_id)
                .where(m.WorkExperience.company.ilike(company))
                .where(m.WorkExperience.role.ilike(role))
            )
        data["start_date"] = _coerce_date(data.get("start_date"))
        data["end_date"] = _coerce_date(data.get("end_date"))
        if existing:
            for k, v in data.items():
                if v is not None and hasattr(existing, k):
                    setattr(existing, k, v)
            return existing
        we = m.WorkExperience(user_id=user_id, **{k: v for k, v in data.items() if v is not None})
        self.session.add(we)
        self.session.flush()
        return we

    def list_work_experience(self, user_id: int) -> list[m.WorkExperience]:
        stmt = select(m.WorkExperience).where(m.WorkExperience.user_id == user_id)
        return list(
            self.session.scalars(stmt.order_by(m.WorkExperience.start_date.desc().nullslast()))
        )

    # --- Education -------------------------------------------------------
    def upsert_education(self, user_id: int, match_id: int | None = None, **data: Any) -> m.Education:
        existing = None
        if match_id is not None:
            existing = self.session.get(m.Education, match_id)
            if existing is not None and existing.user_id != user_id:
                existing = None
        institution = data.get("institution")
        degree = data.get("degree")
        if existing is None and institution:
            stmt = select(m.Education).where(
                m.Education.user_id == user_id,
                m.Education.institution.ilike(institution),
            )
            if degree:
                stmt = stmt.where(m.Education.degree.ilike(degree))
            existing = self.session.scalar(stmt)
        data["start_date"] = _coerce_date(data.get("start_date"))
        data["end_date"] = _coerce_date(data.get("end_date"))
        if existing:
            for k, v in data.items():
                if v is not None and hasattr(existing, k):
                    setattr(existing, k, v)
            return existing
        ed = m.Education(user_id=user_id, **{k: v for k, v in data.items() if v is not None})
        self.session.add(ed)
        self.session.flush()
        return ed

    def list_education(self, user_id: int) -> list[m.Education]:
        stmt = select(m.Education).where(m.Education.user_id == user_id)
        return list(self.session.scalars(stmt.order_by(m.Education.end_date.desc().nullslast())))

    # --- Skills ----------------------------------------------------------
    def upsert_skill(self, user_id: int, match_id: int | None = None, **data: Any) -> m.Skill:
        existing = None
        if match_id is not None:
            existing = self.session.get(m.Skill, match_id)
            if existing is not None and existing.user_id != user_id:
                existing = None
        if existing is None:
            name = data.get("name")
            if name:
                existing = self.session.scalar(
                    select(m.Skill)
                    .where(m.Skill.user_id == user_id)
                    .where(m.Skill.name.ilike(name))
                )
        data["last_used"] = _coerce_date(data.get("last_used"))
        if existing:
            for k, v in data.items():
                if v is not None and hasattr(existing, k):
                    setattr(existing, k, v)
            return existing
        skill = m.Skill(user_id=user_id, **{k: v for k, v in data.items() if v is not None})
        self.session.add(skill)
        self.session.flush()
        return skill

    def list_skills(self, user_id: int, category: str | None = None) -> list[m.Skill]:
        stmt = select(m.Skill).where(m.Skill.user_id == user_id)
        if category:
            stmt = stmt.where(m.Skill.category == category)
        return list(self.session.scalars(stmt.order_by(m.Skill.name)))

    # --- Contacts --------------------------------------------------------
    def upsert_contact(self, user_id: int, match_id: int | None = None, **data: Any) -> m.Contact:
        from src.utils.crypto import encrypt

        existing = None
        if match_id is not None:
            existing = self.session.get(m.Contact, match_id)
            if existing is not None and existing.user_id != user_id:
                existing = None
        if existing is None:
            name = data.get("name")
            if name:
                existing = self.session.scalar(
                    select(m.Contact)
                    .where(m.Contact.user_id == user_id)
                    .where(m.Contact.name.ilike(name))
                )
        data["last_interaction"] = _coerce_date(data.get("last_interaction"))
        if "relationship" in data:
            data["relationship_type"] = data.pop("relationship")
        if data.get("email"):
            data["email"] = encrypt(data["email"])
        if data.get("phone"):
            data["phone"] = encrypt(data["phone"])
        if existing:
            for k, v in data.items():
                if v is not None and hasattr(existing, k):
                    setattr(existing, k, v)
            return existing
        contact = m.Contact(user_id=user_id, **{k: v for k, v in data.items() if v is not None})
        self.session.add(contact)
        self.session.flush()
        return contact

    def list_contacts(self, user_id: int) -> list[m.Contact]:
        stmt = select(m.Contact).where(m.Contact.user_id == user_id)
        return list(self.session.scalars(stmt.order_by(m.Contact.name)))

    # --- Achievements ----------------------------------------------------
    def add_achievement(self, user_id: int, **data: Any) -> m.Achievement:
        data["date"] = _coerce_date(data.get("date"))
        ach = m.Achievement(user_id=user_id, **{k: v for k, v in data.items() if v is not None})
        self.session.add(ach)
        self.session.flush()
        return ach

    # --- Life events -----------------------------------------------------
    def add_life_event(self, user_id: int, **data: Any) -> m.LifeEvent:
        data["date"] = _coerce_date(data.get("date"))
        ev = m.LifeEvent(user_id=user_id, **{k: v for k, v in data.items() if v is not None})
        self.session.add(ev)
        self.session.flush()
        return ev

    # --- Ideas -----------------------------------------------------------
    def add_idea(self, user_id: int, **data: Any) -> m.Idea:
        idea = m.Idea(user_id=user_id, **{k: v for k, v in data.items() if v is not None})
        self.session.add(idea)
        self.session.flush()
        return idea

    # --- Conversations --------------------------------------------------
    def log_message(
        self,
        user_id: int,
        message_text: str,
        role: str,
        intent: str | None = None,
        entities_extracted: dict[str, Any] | None = None,
        telegram_message_id: int | None = None,
    ) -> m.Conversation:
        conv = m.Conversation(
            user_id=user_id,
            message_text=message_text,
            role=role,
            intent=intent,
            entities_extracted=entities_extracted,
            telegram_message_id=telegram_message_id,
        )
        self.session.add(conv)
        self.session.flush()
        return conv

    def recent_messages(self, user_id: int, limit: int = 10) -> list[m.Conversation]:
        # Order by id (monotonic) rather than created_at to keep the ordering
        # stable when multiple messages land in the same second.
        stmt = (
            select(m.Conversation)
            .where(m.Conversation.user_id == user_id)
            .order_by(m.Conversation.id.desc())
            .limit(limit)
        )
        rows = list(self.session.scalars(stmt))
        rows.reverse()
        return rows

    # --- Files -----------------------------------------------------------
    def add_file(self, user_id: int, **data: Any) -> m.FileRecord:
        """Upsert a file record. Telegram returns the same `file_id` for
        identical re-uploads, so we use that as the dedup key. When a
        file already exists for this user with the same telegram_file_id,
        we update its metadata in place instead of creating a duplicate row."""
        cleaned = {k: v for k, v in data.items() if v is not None}
        tg_id = cleaned.get("telegram_file_id")
        existing = None
        if tg_id:
            existing = self.session.execute(
                select(m.FileRecord)
                .where(m.FileRecord.user_id == user_id)
                .where(m.FileRecord.telegram_file_id == tg_id)
            ).scalar_one_or_none()
        if existing is not None:
            for k, v in cleaned.items():
                setattr(existing, k, v)
            self.session.flush()
            return existing
        f = m.FileRecord(user_id=user_id, **cleaned)
        self.session.add(f)
        self.session.flush()
        return f

    def find_file_by_telegram_id(self, user_id: int, telegram_file_id: str) -> m.FileRecord | None:
        if not telegram_file_id:
            return None
        return self.session.execute(
            select(m.FileRecord)
            .where(m.FileRecord.user_id == user_id)
            .where(m.FileRecord.telegram_file_id == telegram_file_id)
        ).scalar_one_or_none()

    # --- Summaries -------------------------------------------------------
    def add_summary(
        self,
        user_id: int,
        period_start: datetime,
        period_end: datetime,
        summary: str,
        key_updates: list[str] | None = None,
        topics: list[str] | None = None,
    ) -> m.MemorySummary:
        s = m.MemorySummary(
            user_id=user_id,
            period_start=period_start,
            period_end=period_end,
            summary=summary,
            key_updates=key_updates,
            topics=topics,
        )
        self.session.add(s)
        self.session.flush()
        return s

    # --- Books -----------------------------------------------------------
    def upsert_book(self, user_id: int, **data: Any) -> m.Book:
        title = data.get("title")
        existing = None
        if title:
            existing = self.session.scalar(
                select(m.Book)
                .where(m.Book.user_id == user_id)
                .where(m.Book.title.ilike(title))
            )
        data["started_at"] = _coerce_date(data.get("started_at"))
        data["finished_at"] = _coerce_date(data.get("finished_at"))
        if existing:
            for k, v in data.items():
                if v is not None and hasattr(existing, k):
                    setattr(existing, k, v)
            return existing
        b = m.Book(user_id=user_id, **{k: v for k, v in data.items() if v is not None})
        self.session.add(b)
        self.session.flush()
        return b

    def list_books(self, user_id: int, status: str | None = None) -> list[m.Book]:
        stmt = select(m.Book).where(m.Book.user_id == user_id)
        if status:
            stmt = stmt.where(m.Book.status == status)
        return list(self.session.scalars(stmt.order_by(m.Book.created_at.desc())))

    # --- Decisions -------------------------------------------------------
    def upsert_decision(self, user_id: int, **data: Any) -> m.Decision:
        title = data.get("title")
        existing = None
        if title:
            existing = self.session.scalar(
                select(m.Decision)
                .where(m.Decision.user_id == user_id)
                .where(m.Decision.title.ilike(title))
            )
        data["decided_at"] = _coerce_date(data.get("decided_at"))
        if existing:
            for k, v in data.items():
                if v is not None and hasattr(existing, k):
                    setattr(existing, k, v)
            return existing
        d = m.Decision(user_id=user_id, **{k: v for k, v in data.items() if v is not None})
        self.session.add(d)
        self.session.flush()
        return d

    def list_decisions(self, user_id: int, status: str | None = None) -> list[m.Decision]:
        stmt = select(m.Decision).where(m.Decision.user_id == user_id)
        if status:
            stmt = stmt.where(m.Decision.status == status)
        return list(self.session.scalars(stmt.order_by(m.Decision.decided_at.desc().nullslast())))

    # --- Health events ---------------------------------------------------
    def add_health_event(self, user_id: int, **data: Any) -> m.HealthEvent:
        data["date"] = _coerce_date(data.get("date"))
        h = m.HealthEvent(user_id=user_id, **{k: v for k, v in data.items() if v is not None})
        self.session.add(h)
        self.session.flush()
        return h

    # --- Quotes ----------------------------------------------------------
    def add_quote(self, user_id: int, **data: Any) -> m.Quote:
        q = m.Quote(user_id=user_id, **{k: v for k, v in data.items() if v is not None})
        self.session.add(q)
        self.session.flush()
        return q

    # --- Job opportunities ----------------------------------------------
    def upsert_job_opportunity(self, user_id: int, **data: Any) -> m.JobOpportunity:
        company = data.get("company")
        role = data.get("role")
        existing = None
        if company and role:
            existing = self.session.scalar(
                select(m.JobOpportunity)
                .where(m.JobOpportunity.user_id == user_id)
                .where(m.JobOpportunity.company.ilike(company))
                .where(m.JobOpportunity.role.ilike(role))
            )
        data["applied_at"] = _coerce_date(data.get("applied_at"))
        data["next_step_at"] = _coerce_date(data.get("next_step_at"))
        if existing:
            for k, v in data.items():
                if v is not None and hasattr(existing, k):
                    setattr(existing, k, v)
            return existing
        j = m.JobOpportunity(user_id=user_id, **{k: v for k, v in data.items() if v is not None})
        self.session.add(j)
        self.session.flush()
        return j

    def list_job_opportunities(self, user_id: int, status: str | None = None) -> list[m.JobOpportunity]:
        stmt = select(m.JobOpportunity).where(m.JobOpportunity.user_id == user_id)
        if status:
            stmt = stmt.where(m.JobOpportunity.status == status)
        return list(self.session.scalars(stmt.order_by(m.JobOpportunity.created_at.desc())))

    # --- CV variants -----------------------------------------------------
    def add_cv_variant(self, user_id: int, **data: Any) -> m.CVVariant:
        v = m.CVVariant(user_id=user_id, **{k: v for k, v in data.items() if v is not None})
        self.session.add(v)
        self.session.flush()
        return v

    def list_cv_variants(self, user_id: int, opportunity_id: int | None = None) -> list[m.CVVariant]:
        stmt = select(m.CVVariant).where(m.CVVariant.user_id == user_id)
        if opportunity_id is not None:
            stmt = stmt.where(m.CVVariant.opportunity_id == opportunity_id)
        return list(self.session.scalars(stmt.order_by(m.CVVariant.created_at.desc())))

    def mark_cv_chosen(self, variant_id: int, feedback: str | None = None) -> m.CVVariant | None:
        v = self.session.get(m.CVVariant, variant_id)
        if v is None:
            return None
        v.chosen = True
        if feedback:
            v.feedback = feedback
        return v

    # --- Audit -----------------------------------------------------------
    def add_audit(
        self,
        user_id: int,
        action: str,
        entity_type: str | None = None,
        entity_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> m.AuditLog:
        entry = m.AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
        )
        self.session.add(entry)
        self.session.flush()
        return entry

    def list_audit(self, user_id: int, limit: int = 50) -> list[m.AuditLog]:
        stmt = (
            select(m.AuditLog)
            .where(m.AuditLog.user_id == user_id)
            .order_by(m.AuditLog.created_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    # --- Goals -----------------------------------------------------------
    def add_goal(
        self,
        user_id: int,
        description: str,
        cadence: str | None = None,
    ) -> m.Goal:
        g = m.Goal(user_id=user_id, description=description, cadence=cadence)
        self.session.add(g)
        self.session.flush()
        return g

    def list_goals(self, user_id: int, status: str | None = None) -> list[m.Goal]:
        stmt = select(m.Goal).where(m.Goal.user_id == user_id)
        if status:
            stmt = stmt.where(m.Goal.status == status)
        return list(self.session.scalars(stmt.order_by(m.Goal.created_at.desc())))

    # --- Weekly reviews --------------------------------------------------
    def add_weekly_review(
        self,
        user_id: int,
        week_start: date,
        wins: list[str] | None = None,
        stuck: list[str] | None = None,
        goals_next_week: list[str] | None = None,
        reflection: str | None = None,
        mood: float | None = None,
    ) -> m.WeeklyReview:
        wr = m.WeeklyReview(
            user_id=user_id,
            week_start=week_start,
            wins=wins,
            stuck=stuck,
            goals_next_week=goals_next_week,
            reflection=reflection,
            mood=mood,
        )
        self.session.add(wr)
        self.session.flush()
        return wr

    def latest_weekly_review(self, user_id: int) -> m.WeeklyReview | None:
        return self.session.scalar(
            select(m.WeeklyReview)
            .where(m.WeeklyReview.user_id == user_id)
            .order_by(m.WeeklyReview.created_at.desc())
            .limit(1)
        )
