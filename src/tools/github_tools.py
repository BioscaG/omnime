"""GitHub primitives — multi-repo. Same client + token can manage any
repo the user owns or has access to.

For each tool, ``repo`` is optional: when omitted, falls back to
``GITHUB_REPO`` (the bot's own repo). That keeps self-edit flows simple
('list my open issues', 'arregla este bug') without losing the ability
to switch ('list issues in BioscaG/atlas').
"""
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
        return None, "GitHub isn't configured (GITHUB_TOKEN missing)."
    return c, None


# --- Read ---------------------------------------------------------------

async def _gh_list_repos(args: dict, context: "Context") -> str:
    c, err = _client_or_disabled()
    if err:
        return err
    try:
        repos = c.list_repos(max_results=int(args.get("max_results") or 30))
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"count": len(repos), "repos": repos}, ensure_ascii=False, default=str)


GH_LIST_REPOS = Tool(
    name="github_list_repos",
    description="List the user's accessible GitHub repos with metadata (name, language, last update).",
    input_schema={
        "type": "object",
        "properties": {"max_results": {"type": "integer", "default": 30, "minimum": 1, "maximum": 100}},
        "required": [],
    },
    run=_gh_list_repos,
)


async def _gh_read_file(args: dict, context: "Context") -> str:
    c, err = _client_or_disabled()
    if err:
        return err
    path = (args.get("path") or "").strip()
    if not path:
        return json.dumps({"error": "path is required"})
    try:
        result = c.read_file(
            path=path,
            ref=(args.get("ref") or None),
            repo=(args.get("repo") or None),
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(result, ensure_ascii=False)


GH_READ_FILE = Tool(
    name="github_read_file",
    description=(
        "Read a file (or list a directory) from a GitHub repo. Use to "
        "inspect source code, docs, or configs across the user's projects "
        "before suggesting changes. ``ref`` defaults to the default branch."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path within the repo (e.g. 'src/brain/orchestrator.py' or 'docs/' for a directory)."},
            "ref": {"type": "string", "description": "Branch / tag / sha. Optional."},
            "repo": {"type": "string", "description": "owner/name. Defaults to GITHUB_REPO (the bot's own)."},
        },
        "required": ["path"],
    },
    run=_gh_read_file,
)


async def _gh_search_code(args: dict, context: "Context") -> str:
    c, err = _client_or_disabled()
    if err:
        return err
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"})
    hits = c.search_code(
        query=query,
        repo=(args.get("repo") or None),
        max_results=int(args.get("max_results") or 15),
    )
    return json.dumps({"query": query, "count": len(hits), "hits": hits}, ensure_ascii=False)


GH_SEARCH_CODE = Tool(
    name="github_search_code",
    description="Search code in a repo (or all repos the user can see) — returns matching files + URLs.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "repo": {"type": "string"},
            "max_results": {"type": "integer", "default": 15},
        },
        "required": ["query"],
    },
    run=_gh_search_code,
)


async def _gh_list_issues(args: dict, context: "Context") -> str:
    c, err = _client_or_disabled()
    if err:
        return err
    state = (args.get("state") or "open").lower()
    if state not in ("open", "closed", "all"):
        state = "open"
    try:
        issues = c.list_issues(
            state=state,
            max_results=int(args.get("max_results") or 20),
            repo=(args.get("repo") or None),
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"state": state, "count": len(issues), "issues": issues}, ensure_ascii=False)


GH_LIST_ISSUES = Tool(
    name="github_list_issues",
    description="List GitHub issues in a repo. Optional state filter (open/closed/all). Without `repo`, uses the bot's own.",
    input_schema={
        "type": "object",
        "properties": {
            "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
            "max_results": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            "repo": {"type": "string"},
        },
        "required": [],
    },
    run=_gh_list_issues,
)


async def _gh_list_pulls(args: dict, context: "Context") -> str:
    c, err = _client_or_disabled()
    if err:
        return err
    state = (args.get("state") or "open").lower()
    if state not in ("open", "closed", "all"):
        state = "open"
    try:
        pulls = c.list_pulls(
            state=state,
            max_results=int(args.get("max_results") or 20),
            repo=(args.get("repo") or None),
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"state": state, "count": len(pulls), "pulls": pulls}, ensure_ascii=False)


GH_LIST_PULLS = Tool(
    name="github_list_pulls",
    description="List pull requests in a repo. Optional `repo` arg.",
    input_schema={
        "type": "object",
        "properties": {
            "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
            "max_results": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            "repo": {"type": "string"},
        },
        "required": [],
    },
    run=_gh_list_pulls,
)


# --- Write --------------------------------------------------------------

async def _gh_create_issue(args: dict, context: "Context") -> str:
    c, err = _client_or_disabled()
    if err:
        return err
    title = (args.get("title") or "").strip()
    body = args.get("body") or ""
    labels = args.get("labels") or []
    if not title:
        return json.dumps({"error": "title is required"})
    try:
        res = c.create_issue(
            title=title, body=body, labels=labels,
            repo=(args.get("repo") or None),
        )
        return json.dumps({"status": "created", **res}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


GH_CREATE_ISSUE = Tool(
    name="github_create_issue",
    description=(
        "Open a new GitHub issue. Confirm with the user before calling — "
        "issues are public on public repos. Optional `repo` arg."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
            "labels": {"type": "array", "items": {"type": "string"}},
            "repo": {"type": "string"},
        },
        "required": ["title"],
    },
    run=_gh_create_issue,
)


async def _gh_create_repo(args: dict, context: "Context") -> str:
    c, err = _client_or_disabled()
    if err:
        return err
    name = (args.get("name") or "").strip()
    if not name:
        return json.dumps({"error": "name is required"})
    description = (args.get("description") or "").strip()
    private = bool(args.get("private", True))
    auto_init = bool(args.get("auto_init", True))
    try:
        result = c.create_repo(
            name=name,
            description=description,
            private=private,
            auto_init=auto_init,
        )
        return json.dumps({"status": "created", **result}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


GH_CREATE_REPO = Tool(
    name="github_create_repo",
    description=(
        "Create a new GitHub repo on the user's account. Defaults to "
        "private + auto_init (so it has an initial commit on main). "
        "Confirm name + visibility with the user before calling — "
        "creating a public repo accidentally leaks code. After creating, "
        "you can scaffold it with claude_code or push code to it manually."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Repo name (kebab-case ideal)."},
            "description": {"type": "string"},
            "private": {"type": "boolean", "default": True},
            "auto_init": {"type": "boolean", "default": True, "description": "Initialise with empty README + main branch."},
        },
        "required": ["name"],
    },
    run=_gh_create_repo,
)


async def _gh_comment_issue(args: dict, context: "Context") -> str:
    c, err = _client_or_disabled()
    if err:
        return err
    n = args.get("number")
    body = (args.get("body") or "").strip()
    if n is None or not body:
        return json.dumps({"error": "number and body are required"})
    try:
        res = c.comment_issue(
            number=int(n), body=body,
            repo=(args.get("repo") or None),
        )
        return json.dumps({"status": "commented", **res}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


GH_COMMENT_ISSUE = Tool(
    name="github_comment_issue",
    description="Post a comment on an existing GitHub issue. Confirm with the user before calling.",
    input_schema={
        "type": "object",
        "properties": {
            "number": {"type": "integer"},
            "body": {"type": "string"},
            "repo": {"type": "string"},
        },
        "required": ["number", "body"],
    },
    run=_gh_comment_issue,
)


def build_github_tools() -> list[Tool]:
    try:
        c = GitHubClient()
        if not c.enabled:
            return []
    except Exception:
        return []
    return [
        GH_LIST_REPOS,
        GH_READ_FILE,
        GH_SEARCH_CODE,
        GH_LIST_ISSUES,
        GH_LIST_PULLS,
        GH_CREATE_REPO,
        GH_CREATE_ISSUE,
        GH_COMMENT_ISSUE,
    ]
