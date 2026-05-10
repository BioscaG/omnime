"""GitHub primitives — issues, PRs, repos."""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from src.integrations.github_client import GitHubClient
from src.tools import Tool

if TYPE_CHECKING:
    from src.brain.context_builder import Context


logger = logging.getLogger(__name__)


def _client_or_disabled() -> tuple[GitHubClient | None, str | None]:
    try:
        c = GitHubClient()
    except Exception as exc:
        return None, f"GitHub error: {exc}"
    if not c.enabled:
        return None, "GitHub isn't configured (GITHUB_TOKEN/GITHUB_REPO missing)."
    return c, None


async def _gh_list_issues(args: dict, context: "Context") -> str:
    client, err = _client_or_disabled()
    if err:
        return err
    state = (args.get("state") or "open").lower()
    if state not in ("open", "closed", "all"):
        state = "open"
    n = int(args.get("max_results") or 20)
    try:
        issues = client.list_issues(state=state, max_results=n)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"state": state, "count": len(issues), "issues": issues}, ensure_ascii=False)


GH_LIST_ISSUES = Tool(
    name="github_list_issues",
    description="List GitHub issues on the configured repo. Filter by state (open/closed/all).",
    input_schema={
        "type": "object",
        "properties": {
            "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
            "max_results": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
        },
        "required": [],
    },
    run=_gh_list_issues,
)


async def _gh_list_pulls(args: dict, context: "Context") -> str:
    client, err = _client_or_disabled()
    if err:
        return err
    state = (args.get("state") or "open").lower()
    if state not in ("open", "closed", "all"):
        state = "open"
    n = int(args.get("max_results") or 20)
    try:
        pulls = client.list_pulls(state=state, max_results=n)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"state": state, "count": len(pulls), "pulls": pulls}, ensure_ascii=False)


GH_LIST_PULLS = Tool(
    name="github_list_pulls",
    description="List pull requests on the configured repo.",
    input_schema={
        "type": "object",
        "properties": {
            "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
            "max_results": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
        },
        "required": [],
    },
    run=_gh_list_pulls,
)


async def _gh_create_issue(args: dict, context: "Context") -> str:
    client, err = _client_or_disabled()
    if err:
        return err
    title = (args.get("title") or "").strip()
    body = args.get("body") or ""
    labels = args.get("labels") or []
    if not title:
        return json.dumps({"error": "title is required"})
    try:
        res = client.create_issue(title=title, body=body, labels=labels)
        return json.dumps({"status": "created", **res}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


GH_CREATE_ISSUE = Tool(
    name="github_create_issue",
    description=(
        "Open a new GitHub issue on the configured repo. Confirm with the "
        "user before calling — issues are public on public repos."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
            "labels": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title"],
    },
    run=_gh_create_issue,
)


def build_github_tools() -> list[Tool]:
    try:
        c = GitHubClient()
        if not c.enabled:
            return []
    except Exception:
        return []
    return [GH_LIST_ISSUES, GH_LIST_PULLS, GH_CREATE_ISSUE]
