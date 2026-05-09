"""Deploy generated skills: write file, hot-reload, optional GitHub commit."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from src.config import settings
from src.skills.registry import SkillRegistry


logger = logging.getLogger(__name__)


class Deployer:
    def __init__(self, registry: SkillRegistry, skills_dir: Path | None = None) -> None:
        self.registry = registry
        self.skills_dir = skills_dir or (settings.project_root / "src" / "skills")

    @staticmethod
    def slug(name: str) -> str:
        s = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
        return s or "skill"

    def deploy(self, skill_name: str, code: str, push_to_github: bool = False) -> dict[str, Any]:
        slug = self.slug(skill_name)
        path = self.skills_dir / f"{slug}.py"
        if path.exists():
            raise FileExistsError(f"Skill file already exists: {path}")
        path.write_text(code, encoding="utf-8")

        if not self.registry.reload(slug):
            module = self._load_module(slug)
            cls = self._find_skill_class(module)
            if cls is None:
                raise RuntimeError("No BaseSkill subclass found in generated code")
            instance = cls(self.registry.llm, self.registry.memory)
            self.registry.register(instance)

        commit_info: dict[str, Any] = {}
        if push_to_github:
            try:
                from src.integrations.github_client import GitHubClient

                client = GitHubClient()
                if client.enabled:
                    commit_info = client.commit_file(
                        path=f"src/skills/{slug}.py",
                        content=code,
                        message=f"Add skill: {skill_name}",
                    )
            except Exception as exc:
                logger.warning("GitHub commit failed: %s", exc)
                commit_info = {"error": str(exc)}

        return {"path": str(path), "slug": slug, "github": commit_info}

    @staticmethod
    def _load_module(slug: str):
        import importlib

        return importlib.import_module(f"src.skills.{slug}")

    @staticmethod
    def _find_skill_class(module):
        from src.skills.base import BaseSkill

        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and issubclass(obj, BaseSkill) and obj is not BaseSkill:
                return obj
        return None
