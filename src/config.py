"""Centralized configuration loaded from environment variables."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Required ---
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_user_id: int = Field(default=0, alias="TELEGRAM_USER_ID")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # --- LLM ---
    llm_provider: Literal["anthropic", "openai", "ollama"] = Field(
        default="anthropic", alias="LLM_PROVIDER"
    )
    llm_model_fast: str = Field(
        default="claude-sonnet-4-20250514", alias="LLM_MODEL_FAST"
    )
    llm_model_powerful: str = Field(
        default="claude-opus-4-20250514", alias="LLM_MODEL_POWERFUL"
    )
    llm_fallback_provider: Optional[Literal["anthropic", "openai", "ollama"]] = Field(
        default=None, alias="LLM_FALLBACK_PROVIDER"
    )
    llm_fallback_model: Optional[str] = Field(default=None, alias="LLM_FALLBACK_MODEL")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    ollama_host: str = Field(default="http://localhost:11434", alias="OLLAMA_HOST")
    ollama_model: str = Field(default="llama3", alias="OLLAMA_MODEL")

    # --- Database ---
    db_host: str = Field(default="postgres", alias="DB_HOST")
    db_port: int = Field(default=5432, alias="DB_PORT")
    db_name: str = Field(default="omnime", alias="DB_NAME")
    db_user: str = Field(default="omnime", alias="DB_USER")
    db_password: str = Field(default="omnime", alias="DB_PASSWORD")

    # --- ChromaDB ---
    chroma_host: str = Field(default="chromadb", alias="CHROMA_HOST")
    chroma_port: int = Field(default=8000, alias="CHROMA_PORT")

    # --- Gmail ---
    gmail_client_id: str = Field(default="", alias="GMAIL_CLIENT_ID")
    gmail_client_secret: str = Field(default="", alias="GMAIL_CLIENT_SECRET")
    gmail_refresh_token: str = Field(default="", alias="GMAIL_REFRESH_TOKEN")

    # --- Calendar ---
    gcal_client_id: str = Field(default="", alias="GCAL_CLIENT_ID")
    gcal_client_secret: str = Field(default="", alias="GCAL_CLIENT_SECRET")
    gcal_refresh_token: str = Field(default="", alias="GCAL_REFRESH_TOKEN")

    # --- GitHub ---
    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    github_repo: str = Field(default="", alias="GITHUB_REPO")

    # --- Notion ---
    notion_token: str = Field(default="", alias="NOTION_TOKEN")
    notion_projects_db: str = Field(default="", alias="NOTION_PROJECTS_DB")
    notion_ideas_db: str = Field(default="", alias="NOTION_IDEAS_DB")
    notion_contacts_db: str = Field(default="", alias="NOTION_CONTACTS_DB")

    # --- Self-evolution ---
    evolution_use_docker: bool = Field(default=True, alias="EVOLUTION_USE_DOCKER")

    # --- General ---
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    timezone: str = Field(default="UTC", alias="TIMEZONE")
    daily_briefing_time: str = Field(default="08:00", alias="DAILY_BRIEFING_TIME")
    language: str = Field(default="auto", alias="LANGUAGE")
    encryption_key: str = Field(default="", alias="ENCRYPTION_KEY")

    # --- Telegram runtime ---
    telegram_mode: Literal["polling", "webhook"] = Field(
        default="polling", alias="TELEGRAM_MODE"
    )
    webhook_url: str = Field(default="", alias="WEBHOOK_URL")
    webhook_port: int = Field(default=8443, alias="WEBHOOK_PORT")

    # --- Paths ---
    project_root: Path = PROJECT_ROOT
    prompts_dir: Path = PROJECT_ROOT / "prompts"
    data_dir: Path = PROJECT_ROOT / "data"
    exports_dir: Path = PROJECT_ROOT / "data" / "exports"
    backups_dir: Path = PROJECT_ROOT / "data" / "backups"
    uploads_dir: Path = PROJECT_ROOT / "data" / "uploads"

    @field_validator("daily_briefing_time")
    @classmethod
    def _check_time(cls, v: str) -> str:
        try:
            hh, mm = v.split(":")
            if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                raise ValueError
        except Exception as exc:
            raise ValueError("daily_briefing_time must be in HH:MM format") from exc
        return v

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def async_database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    def ensure_directories(self) -> None:
        for d in (self.exports_dir, self.backups_dir, self.uploads_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
