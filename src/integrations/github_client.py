"""GitHub wrapper. Supports multiple repositories — every method takes
an optional ``repo`` argument; when omitted, falls back to
``settings.github_repo`` (the bot's own repo for self-evolution).
"""
from __future__ import annotations

import logging
from typing import Any

from src.config import settings


logger = logging.getLogger(__name__)


class GitHubClient:
    def __init__(self) -> None:
        self._gh = None
        self._repo_cache: dict[str, Any] = {}
        self.enabled = bool(settings.github_token)
        self.default_repo = settings.github_repo or ""

    def _build_gh(self):
        if self._gh is not None:
            return self._gh
        if not self.enabled:
            raise RuntimeError("GitHub not configured (GITHUB_TOKEN missing)")
        from github import Github

        self._gh = Github(settings.github_token)
        return self._gh

    def _repo(self, repo: str | None = None):
        full_name = repo or self.default_repo
        if not full_name:
            raise RuntimeError("no repo specified and no default GITHUB_REPO set")
        if full_name in self._repo_cache:
            return self._repo_cache[full_name]
        gh = self._build_gh()
        r = gh.get_repo(full_name)
        self._repo_cache[full_name] = r
        return r

    # --- Reading -------------------------------------------------------

    def read_file(self, path: str, ref: str | None = None, repo: str | None = None) -> dict[str, Any]:
        r = self._repo(repo)
        contents = r.get_contents(path, ref=ref) if ref else r.get_contents(path)
        if isinstance(contents, list):
            return {
                "path": path,
                "kind": "directory",
                "entries": [c.name for c in contents],
            }
        return {
            "path": contents.path,
            "kind": "file",
            "size": contents.size,
            "sha": contents.sha,
            "content": contents.decoded_content.decode("utf-8", errors="replace") if contents.size < 200_000 else "(too large)",
        }

    def list_repos(self, max_results: int = 50) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        gh = self._build_gh()
        out = []
        for r in gh.get_user().get_repos()[:max_results]:
            out.append({
                "full_name": r.full_name,
                "private": r.private,
                "description": r.description,
                "language": r.language,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            })
        return out

    def search_code(
        self,
        query: str,
        repo: str | None = None,
        max_results: int = 15,
    ) -> list[dict[str, Any]]:
        gh = self._build_gh()
        full = repo or self.default_repo
        q = f"{query} repo:{full}" if full else query
        try:
            results = gh.search_code(q)
            out = []
            for hit in results[:max_results]:
                out.append({
                    "path": hit.path,
                    "repo": hit.repository.full_name,
                    "url": hit.html_url,
                })
            return out
        except Exception as exc:
            logger.warning("search_code failed: %s", exc)
            return []

    def list_issues(
        self,
        state: str = "open",
        max_results: int = 20,
        repo: str | None = None,
    ) -> list[dict[str, Any]]:
        r = self._repo(repo)
        out = []
        for issue in r.get_issues(state=state)[:max_results]:
            if issue.pull_request is not None:
                continue
            out.append({
                "number": issue.number,
                "title": issue.title,
                "url": issue.html_url,
                "state": issue.state,
                "labels": [l.name for l in issue.labels],
                "updated_at": issue.updated_at.isoformat() if issue.updated_at else None,
                "body": (issue.body or "")[:1500],
            })
        return out

    def list_pulls(
        self,
        state: str = "open",
        max_results: int = 20,
        repo: str | None = None,
    ) -> list[dict[str, Any]]:
        r = self._repo(repo)
        out = []
        for pr in r.get_pulls(state=state)[:max_results]:
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

    # --- Writing -------------------------------------------------------

    def commit_file(
        self,
        path: str,
        content: str,
        message: str,
        branch: str = "main",
        create_branch_from: str | None = None,
        repo: str | None = None,
    ) -> dict[str, Any]:
        r = self._repo(repo)
        if create_branch_from and branch != create_branch_from:
            self._ensure_branch(branch, create_branch_from, repo=repo)
        try:
            existing = r.get_contents(path, ref=branch)
            res = r.update_file(path, message, content, existing.sha, branch=branch)
        except Exception:
            res = r.create_file(path, message, content, branch=branch)
        return {"commit": res["commit"].sha, "path": path, "branch": branch}

    def _ensure_branch(self, branch: str, source: str, repo: str | None = None) -> None:
        r = self._repo(repo)
        try:
            r.get_branch(branch)
            return
        except Exception:
            pass
        src = r.get_branch(source)
        r.create_git_ref(ref=f"refs/heads/{branch}", sha=src.commit.sha)

    def open_pr(
        self,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        repo: str | None = None,
    ) -> dict[str, Any]:
        r = self._repo(repo)
        pr = r.create_pull(title=title, body=body, head=head, base=base)
        return {"number": pr.number, "url": pr.html_url}

    def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
        repo: str | None = None,
    ) -> dict[str, Any]:
        r = self._repo(repo)
        issue = r.create_issue(title=title, body=body, labels=labels or [])
        return {"number": issue.number, "url": issue.html_url}

    def comment_issue(
        self,
        number: int,
        body: str,
        repo: str | None = None,
    ) -> dict[str, Any]:
        r = self._repo(repo)
        issue = r.get_issue(number)
        comment = issue.create_comment(body)
        return {"id": comment.id, "url": comment.html_url}

    def create_repo(
        self,
        name: str,
        description: str = "",
        private: bool = True,
        auto_init: bool = True,
    ) -> dict[str, Any]:
        gh = self._build_gh()
        user = gh.get_user()
        repo = user.create_repo(
            name=name,
            description=description,
            private=private,
            auto_init=auto_init,
        )
        return {
            "full_name": repo.full_name,
            "url": repo.html_url,
            "clone_url": repo.clone_url,
            "private": repo.private,
        }
