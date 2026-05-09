"""GitHub wrapper used by self-evolution to commit new skills."""
from __future__ import annotations

import logging
from typing import Any

from src.config import settings


logger = logging.getLogger(__name__)


class GitHubClient:
    def __init__(self) -> None:
        self._gh = None
        self._repo = None
        self.enabled = bool(settings.github_token and settings.github_repo)

    def _build(self):
        if self._repo is not None:
            return self._repo
        if not self.enabled:
            raise RuntimeError("GitHub not configured")
        from github import Github

        self._gh = Github(settings.github_token)
        self._repo = self._gh.get_repo(settings.github_repo)
        return self._repo

    def commit_file(
        self,
        path: str,
        content: str,
        message: str,
        branch: str = "main",
    ) -> dict[str, Any]:
        repo = self._build()
        try:
            existing = repo.get_contents(path, ref=branch)
            res = repo.update_file(path, message, content, existing.sha, branch=branch)
        except Exception:
            res = repo.create_file(path, message, content, branch=branch)
        return {"commit": res["commit"].sha, "path": path}

    def open_pr(self, title: str, body: str, head: str, base: str = "main") -> dict[str, Any]:
        repo = self._build()
        pr = repo.create_pull(title=title, body=body, head=head, base=base)
        return {"number": pr.number, "url": pr.html_url}

    def list_repos(self) -> list[str]:
        if not self.enabled:
            return []
        from github import Github

        gh = self._gh or Github(settings.github_token)
        return [r.full_name for r in gh.get_user().get_repos()]

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> dict[str, Any]:
        repo = self._build()
        issue = repo.create_issue(title=title, body=body, labels=labels or [])
        return {"number": issue.number, "url": issue.html_url}
