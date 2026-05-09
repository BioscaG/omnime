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
    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["UserProfile"] = relationship(back_populates="ideas")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    intent: Mapped[Optional[str]] = mapped_column(String(50))
    entities_extracted: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
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
