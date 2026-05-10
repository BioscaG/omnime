"""Self-edit primitives — let the bot read its own source and propose
changes via GitHub PR. Used when the user reports a bug ('cuando hago X
falla con Y') or wants a small improvement.

Reading is done from the local /app filesystem (the running container's
source) — faster than GitHub API and always reflects what's actually
deployed. Writing goes through GitHub: branch + commit + PR. Auto-deploy
only fires after PR merge to main, so the user always reviews before any
change goes live.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from src.tools import Tool

if TYPE_CHECKING:
    from src.brain.context_builder import Context


logger = logging.getLogger(__name__)


# Allow reads/proposals only inside the project tree. Prevents the agent
# from wandering into /etc/passwd or other system files.
SOURCE_ROOT = Path("/app").resolve() if Path("/app").exists() else Path.cwd().resolve()
ALLOWED_SUBTREES = ("src/", "tests/", "scripts/", "alembic/", "docs/", "prompts/", "docker-compose.yml", "Dockerfile", "requirements.txt", "README.md")


def _safe_path(rel: str) -> Path | None:
    """Resolve ``rel`` against SOURCE_ROOT, ensuring it stays inside the
    repo and starts with one of the allowed subtrees."""
    if not rel:
        return None
    rel = rel.lstrip("/")
    if not any(rel == top.rstrip("/") or rel.startswith(top) for top in ALLOWED_SUBTREES):
        return None
    p = (SOURCE_ROOT / rel).resolve()
    try:
        p.relative_to(SOURCE_ROOT)
    except ValueError:
        return None
    return p


# --- Read --------------------------------------------------------------

async def _bot_read_source(args: dict, context: "Context") -> str:
    rel = (args.get("path") or "").strip()
    p = _safe_path(rel)
    if p is None:
        return json.dumps({"error": f"path not allowed or out of tree: {rel!r}"})
    if not p.exists():
        return json.dumps({"error": f"not found: {rel}"})
    if p.is_dir():
        entries = sorted(child.name for child in p.iterdir())
        return json.dumps({"path": rel, "kind": "directory", "entries": entries[:200]})
    if p.stat().st_size > 200_000:
        return json.dumps({"error": "file too large (>200KB), narrow your read"})
    return json.dumps({
        "path": rel,
        "kind": "file",
        "size": p.stat().st_size,
        "content": p.read_text(encoding="utf-8", errors="replace"),
    })


BOT_READ_SOURCE = Tool(
    name="bot_read_source",
    description=(
        "Read a file (or list a directory) from the bot's own source tree. "
        "Use to understand HOW the bot works before proposing a change. "
        "Allowed roots: src/, tests/, scripts/, alembic/, docs/, "
        "prompts/, Dockerfile, docker-compose.yml, requirements.txt."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repo-relative path (e.g. 'src/brain/orchestrator.py' or 'src/tools/')."},
        },
        "required": ["path"],
    },
    run=_bot_read_source,
)


async def _bot_grep_source(args: dict, context: "Context") -> str:
    pattern = (args.get("pattern") or "").strip()
    if not pattern:
        return json.dumps({"error": "pattern is required"})
    glob = (args.get("glob") or "src/**/*.py").strip()
    max_results = int(args.get("max_results") or 30)
    if any(c in glob for c in (";", "|", "$", "`")):
        return json.dumps({"error": "glob looks suspicious"})
    try:
        # Use plain grep -rn for simplicity; restrict via -- and the glob.
        cmd = [
            "grep", "-rn", "-E", pattern,
            "--include", glob.split("/")[-1] if "*" in glob else glob,
            str(SOURCE_ROOT / glob.split("/")[0]) if not glob.startswith("/") else glob,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        lines = r.stdout.splitlines()[:max_results]
        # Strip the absolute prefix so paths are repo-relative.
        prefix = str(SOURCE_ROOT) + "/"
        cleaned = [line.replace(prefix, "") for line in lines]
        return json.dumps({"pattern": pattern, "count": len(cleaned), "matches": cleaned})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


BOT_GREP_SOURCE = Tool(
    name="bot_grep_source",
    description=(
        "grep -E across the bot's source tree. Use to find where a function, "
        "class, env var, or string lives before proposing a change. Default "
        "glob: src/**/*.py."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex (extended) pattern."},
            "glob": {"type": "string", "description": "Path glob, e.g. 'src/**/*.py' (default) or 'tests/**/*.py'."},
            "max_results": {"type": "integer", "default": 30, "minimum": 1, "maximum": 200},
        },
        "required": ["pattern"],
    },
    run=_bot_grep_source,
)


# --- Write — propose a change via PR ------------------------------------

async def _bot_propose_change(args: dict, context: "Context") -> str:
    """Create a branch, commit one or more file changes, open a PR. The user
    reviews + merges manually (which auto-deploys via GitHub Actions)."""
    description = (args.get("description") or "").strip()
    title = (args.get("title") or description.split("\n")[0][:80] or "OMNIME self-edit").strip()
    files = args.get("files") or []
    if not isinstance(files, list) or not files:
        return json.dumps({"error": "files must be a non-empty list of {path, content}"})
    if not description:
        return json.dumps({"error": "description is required (will be the PR body)"})

    from src.integrations.github_client import GitHubClient

    client = GitHubClient()
    if not client.enabled:
        return json.dumps({"error": "GitHub not configured (GITHUB_TOKEN/GITHUB_REPO missing)"})

    branch_slug = (args.get("branch") or _slugify(title)).strip()
    branch = f"omnime-self/{branch_slug}"[:60]

    # Validate every path before any GitHub call so we fail atomically.
    for f in files:
        rel = (f.get("path") or "").strip()
        if _safe_path(rel) is None:
            return json.dumps({"error": f"path not allowed: {rel!r}"})
        if "content" not in f or not isinstance(f["content"], str):
            return json.dumps({"error": f"file {rel}: 'content' must be a string"})

    try:
        for f in files:
            client.commit_file(
                path=f["path"],
                content=f["content"],
                message=f"omnime self-edit: {title} ({f['path']})",
                branch=branch,
                create_branch_from="main",
            )
        pr = client.open_pr(
            title=title,
            body=(
                f"{description}\n\n"
                f"---\n_Generated by OMNIME self-edit. Review and merge "
                f"to deploy. Touched files: {len(files)}_"
            ),
            head=branch,
            base="main",
        )
    except Exception as exc:
        logger.exception("bot_propose_change failed")
        return json.dumps({"error": str(exc)})

    return json.dumps({
        "status": "pr_opened",
        "branch": branch,
        "pr_number": pr.get("number"),
        "pr_url": pr.get("url"),
        "files_changed": [f["path"] for f in files],
    })


def _slugify(text: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "change"


BOT_PROPOSE_CHANGE = Tool(
    name="bot_propose_change",
    description=(
        "Open a GitHub PR with one or more file changes to the OMNIME repo "
        "itself. Use to FIX bugs you've identified or to add small "
        "improvements. The PR is reviewed by the user before merge — "
        "merging triggers auto-deploy. NEVER call this without first using "
        "bot_read_source / bot_grep_source to understand existing code, "
        "and the user must explicitly say 'arregla esto' / 'haz el cambio'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "PR title (concise, imperative)."},
            "description": {"type": "string", "description": "PR body — what changed and why."},
            "branch": {"type": "string", "description": "Optional branch slug. Auto-generated from title if omitted."},
            "files": {
                "type": "array",
                "description": "List of files to write. Each item is {path, content}. content REPLACES the file.",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        "required": ["description", "files"],
    },
    run=_bot_propose_change,
)


def build_self_tools() -> list[Tool]:
    """Self-edit tools always available — reading the source has no cost
    beyond a disk read; bot_propose_change auto-disables when GitHub
    isn't configured (returns an error string)."""
    return [BOT_READ_SOURCE, BOT_GREP_SOURCE, BOT_PROPOSE_CHANGE]
