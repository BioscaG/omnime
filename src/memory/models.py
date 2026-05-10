"""SQLAlchemy ORM models for the structured memory layer."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserProfile(Base):
    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255))
    bio: Mapped[Optional[str]] = mapped_column(Text)
    communication_style: Mapped[Optional[str]] = mapped_column(Text)
    personality_traits: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    preferences: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    living_profile: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    projects: Mapped[list["Project"]] = relationship(back_populates="user")
    work_experience: Mapped[list["WorkExperience"]] = relationship(back_populates="user")
    education: Mapped[list["Education"]] = relationship(back_populates="user")
    skills: Mapped[list["Skill"]] = relationship(back_populates="user")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="user")
    achievements: Mapped[list["Achievement"]] = relationship(back_populates="user")
    life_events: Mapped[list["LifeEvent"]] = relationship(back_populates="user")
    ideas: Mapped[list["Idea"]] = relationship(back_populates="user")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    role: Mapped[Optional[str]] = mapped_column(String(255))
    technologies: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(50), default="active")
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    key_achievements: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    challenges: Mapped[Optional[str]] = mapped_column(Text)
    collaborators: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    links: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    details: Mapped[Optional[str]] = mapped_column(Text)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    last_referenced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["UserProfile"] = relationship(back_populates="projects")
    achievements: Mapped[list["Achievement"]] = relationship(back_populates="project")


class WorkExperience(Base):
    __tablename__ = "work_experience"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    achievements: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    technologies: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    location: Mapped[Optional[str]] = mapped_column(String(255))
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["UserProfile"] = relationship(back_populates="work_experience")


class Education(Base):
    __tablename__ = "education"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    institution: Mapped[str] = mapped_column(String(255), nullable=False)
    degree: Mapped[Optional[str]] = mapped_column(String(255))
    field: Mapped[Optional[str]] = mapped_column(String(255))
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    grade: Mapped[Optional[str]] = mapped_column(String(50))
    achievements: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    courses: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    thesis: Mapped[Optional[str]] = mapped_column(Text)
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["UserProfile"] = relationship(back_populates="education")


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100))
    proficiency: Mapped[Optional[str]] = mapped_column(String(50))
    years_experience: Mapped[Optional[float]] = mapped_column(Float)
    context: Mapped[Optional[str]] = mapped_column(Text)
    last_used: Mapped[Optional[date]] = mapped_column(Date)
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["UserProfile"] = relationship(back_populates="skills")


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    relationship_type: Mapped[Optional[str]] = mapped_column("relationship", String(100))
    organization: Mapped[Optional[str]] = mapped_column(String(255))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    last_interaction: Mapped[Optional[date]] = mapped_column(Date)
    context: Mapped[Optional[str]] = mapped_column(Text)
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["UserProfile"] = relationship(back_populates="contacts")


class Achievement(Base):
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    date: Mapped[Optional[date]] = mapped_column(Date)
    category: Mapped[Optional[str]] = mapped_column(String(100))
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id"))
    impact: Mapped[Optional[str]] = mapped_column(Text)
    evidence: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["UserProfile"] = relationship(back_populates="achievements")
    project: Mapped[Optional["Project"]] = relationship(back_populates="achievements")


class LifeEvent(Base):
    __tablename__ = "life_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    date: Mapped[Optional[date]] = mapped_column(Date)
    category: Mapped[Optional[str]] = mapped_column(String(100))
    location: Mapped[Optional[str]] = mapped_column(String(255))
    people_involved: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    lessons_learned: Mapped[Optional[str]] = mapped_column(Text)
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["UserProfile"] = relationship(back_populates="life_events")


class Idea(Base):
    __tablename__ = "ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100))
    tags: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(50), default="raw")
    related_project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id"))
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    last_referenced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["UserProfile"] = relationship(back_populates="ideas")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[Optional[str]] = mapped_column(String(100))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    cadence: Mapped[Optional[str]] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50), default="active")
    streak: Mapped[int] = mapped_column(Integer, default=0)
    longest_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_check_in: Mapped[Optional[date]] = mapped_column(Date)
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    author: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), default="reading")
    rating: Mapped[Optional[float]] = mapped_column(Float)
    started_at: Mapped[Optional[date]] = mapped_column(Date)
    finished_at: Mapped[Optional[date]] = mapped_column(Date)
    takeaways: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    quotes: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    source: Mapped[Optional[str]] = mapped_column(String(255))
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    alternatives: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    outcome: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    decided_at: Mapped[Optional[date]] = mapped_column(Date)
    category: Mapped[Optional[str]] = mapped_column(String(100))
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class HealthEvent(Base):
    __tablename__ = "health_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100))
    date: Mapped[Optional[date]] = mapped_column(Date)
    description: Mapped[Optional[str]] = mapped_column(Text)
    severity: Mapped[Optional[str]] = mapped_column(String(50))
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(500))
    author: Mapped[Optional[str]] = mapped_column(String(255))
    tags: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class JobOpportunity(Base):
    __tablename__ = "job_opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), default="discovered")
    description: Mapped[Optional[str]] = mapped_column(Text)
    location: Mapped[Optional[str]] = mapped_column(String(255))
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    salary_range: Mapped[Optional[str]] = mapped_column(String(100))
    link: Mapped[Optional[str]] = mapped_column(String(500))
    contact: Mapped[Optional[str]] = mapped_column(String(255))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    applied_at: Mapped[Optional[date]] = mapped_column(Date)
    next_step_at: Mapped[Optional[date]] = mapped_column(Date)
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class CVVariant(Base):
    __tablename__ = "cv_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    opportunity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("job_opportunities.id", ondelete="SET NULL")
    )
    label: Mapped[Optional[str]] = mapped_column(String(255))
    body_markdown: Mapped[Optional[str]] = mapped_column(Text)
    highlights: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    style: Mapped[Optional[str]] = mapped_column(String(100))
    score: Mapped[Optional[float]] = mapped_column(Float)
    chosen: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback: Mapped[Optional[str]] = mapped_column(Text)
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class WeeklyReview(Base):
    __tablename__ = "weekly_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    wins: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    stuck: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    goals_next_week: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    reflection: Mapped[Optional[str]] = mapped_column(Text)
    mood: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    intent: Mapped[Optional[str]] = mapped_column(String(50))
    entities_extracted: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    sentiment: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["UserProfile"] = relationship(back_populates="conversations")


class MemorySummary(Base):
    __tablename__ = "memory_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    key_updates: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    topics: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Credential(Base):
    __tablename__ = "credentials"
    __table_args__ = (
        # Composite uniqueness keeps one row per (user, site).
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    site: Mapped[str] = mapped_column(String(255), nullable=False)
    login_url: Mapped[Optional[str]] = mapped_column(String(500))
    username_enc: Mapped[Optional[str]] = mapped_column(Text)
    password_enc: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class BrowserSession(Base):
    __tablename__ = "browser_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="running")
    steps_taken: Mapped[int] = mapped_column(Integer, default=0)
    final_url: Mapped[Optional[str]] = mapped_column(String(1000))
    transcript: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class BrowserRecipe(Base):
    __tablename__ = "browser_recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    goal_template: Mapped[str] = mapped_column(Text, nullable=False)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    uses: Mapped[int] = mapped_column(Integer, default=1)
    successes: Mapped[int] = mapped_column(Integer, default=1)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BrowserSiteNote(Base):
    __tablename__ = "browser_site_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(50), default="observation")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FileRecord(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    filename: Mapped[Optional[str]] = mapped_column(String(500))
    file_type: Mapped[Optional[str]] = mapped_column(String(100))
    telegram_file_id: Mapped[Optional[str]] = mapped_column(String(255))
    extracted_text: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    args: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    result_preview: Mapped[Optional[str]] = mapped_column(Text)
    turn_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    evidence_count: Mapped[int] = mapped_column(Integer, default=1)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[Optional[str]] = mapped_column(Text)
    linked_kind: Mapped[Optional[str]] = mapped_column(String(40))
    linked_id: Mapped[Optional[int]] = mapped_column(Integer)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
