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
        create_branch_from: str | None = None,
    ) -> dict[str, Any]:
        repo = self._build()
        if create_branch_from and branch != create_branch_from:
            self._ensure_branch(branch, create_branch_from)
        try:
            existing = repo.get_contents(path, ref=branch)
            res = repo.update_file(path, message, content, existing.sha, branch=branch)
        except Exception:
            res = repo.create_file(path, message, content, branch=branch)
        return {"commit": res["commit"].sha, "path": path, "branch": branch}

    def _ensure_branch(self, branch: str, source: str) -> None:
        repo = self._build()
        try:
            repo.get_branch(branch)
            return
        except Exception:
            pass
        src = repo.get_branch(source)
        repo.create_git_ref(ref=f"refs/heads/{branch}", sha=src.commit.sha)

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

    def list_issues(self, state: str = "open", max_results: int = 20) -> list[dict[str, Any]]:
        repo = self._build()
        out = []
        for issue in repo.get_issues(state=state)[:max_results]:
            if issue.pull_request is not None:
                continue  # skip PRs
            out.append({
                "number": issue.number,
                "title": issue.title,
                "url": issue.html_url,
                "state": issue.state,
                "labels": [l.name for l in issue.labels],
                "updated_at": issue.updated_at.isoformat() if issue.updated_at else None,
            })
        return out

    def list_pulls(self, state: str = "open", max_results: int = 20) -> list[dict[str, Any]]:
        repo = self._build()
        out = []
        for pr in repo.get_pulls(state=state)[:max_results]:
            out.append({
                "number": pr.number,
                "title": pr.title,
                "url": pr.html_url,
                "state": pr.state,
                "draft": pr.draft,
                "head": pr.head.ref,
                "base": pr.base.ref,
                "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
            })
        return out
