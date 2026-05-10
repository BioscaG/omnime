"""Deploy generated skills: write file, hot-reload, branch + PR on GitHub."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import settings
from src.evolution.ast_validator import validate
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

    def deploy(
        self,
        skill_name: str,
        code: str,
        push_to_github: bool = False,
        open_pr: bool = True,
    ) -> dict[str, Any]:
        # Re-validate before writing — sandbox check ran earlier but the file
        # has not yet been committed to disk; running twice is cheap insurance.
        report = validate(code)
        if not report.ok:
            raise ValueError(f"Generated code failed validation: {report.first_reason}")

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
            commit_info = self._push_to_github(slug, code, skill_name, open_pr=open_pr)

        return {"path": str(path), "slug": slug, "github": commit_info}

    def _push_to_github(self, slug: str, code: str, skill_name: str, open_pr: bool) -> dict[str, Any]:
        try:
            from src.integrations.github_client import GitHubClient

            client = GitHubClient()
            if not client.enabled:
                return {"skipped": "GitHub not configured"}
            branch = f"evolve/{slug}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
            commit = client.commit_file(
                path=f"src/skills/{slug}.py",
                content=code,
                message=f"Add skill: {skill_name}",
                branch=branch,
                create_branch_from="main",
            )
            info: dict[str, Any] = {"branch": branch, **commit}
            if open_pr:
                pr = client.open_pr(
                    title=f"[evolve] {skill_name}",
                    body=(
                        f"Auto-generated skill `{slug}` via /evolve.\n\n"
                        "AST allowlist passed; sandbox smoke test passed.\n"
                        "Review the file before merging."
                    ),
                    head=branch,
                    base="main",
                )
                info["pr"] = pr
            return info
        except Exception as exc:
            logger.warning("GitHub push failed: %s", exc)
            return {"error": str(exc)}

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
