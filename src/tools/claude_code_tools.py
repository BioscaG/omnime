"""Claude Code integration — the bot's real programming tool.

Three flows:

1. **Self-edit**: ``claude_code(prompt, repo='self')`` — clone OMNIME, run
   Claude Code, open PR back to it.
2. **Existing repo**: ``claude_code(prompt, repo='owner/name')`` — same but
   for any repo the GITHUB_TOKEN can access.
3. **New project**: ``claude_code_new_project(name, description, prompt)``
   — create a fresh GitHub repo, scaffold via Claude Code, push.

The CLI authenticates via the user's Pro/Max subscription if
``data/claude-auth/`` is mounted at ``/root/.claude`` (subscription
flow, $0 per call within plan limits) or via ``ANTHROPIC_API_KEY``
(pay-as-you-go).

Safety:
- Always opens a PR; never pushes to ``main`` of any repo directly.
- ``--dangerously-skip-permissions`` is OK because Claude Code runs in
  a fresh ``/tmp/<random>`` worktree with no access to /app/.
- Hard timeout (default 300s, max 600s).
- The system prompt forbids the agent from calling these tools without
  explicit user phrasing ('usa claude code', 'with claude code',
  'open a PR with claude code', etc.).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from src.config import settings
from src.integrations.github_client import GitHubClient
from src.tools import Tool

if TYPE_CHECKING:
    from src.brain.context_builder import Context


logger = logging.getLogger(__name__)


MAX_TIMEOUT = 600
DEFAULT_TIMEOUT = 300


def _has_claude_cli() -> bool:
    return shutil.which("claude") is not None


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:50] or "claude-code"


def _authed_clone_url(full_name: str) -> str | None:
    """Return an HTTPS URL with embedded token for clone/push."""
    if not settings.github_token:
        return None
    return f"https://x-access-token:{settings.github_token}@github.com/{full_name}.git"


def _run_claude(prompt: str, cwd: Path, timeout: int) -> tuple[bool, str, str]:
    """Run Claude Code non-interactively in cwd. Returns (ok, stdout, stderr)."""
    env = os.environ.copy()
    # If ANTHROPIC_API_KEY is set, Claude Code will use it. If not, it falls
    # back to the mounted ~/.claude credentials (Pro/Max subscription).
    cmd = [
        "claude",
        "-p", prompt,
        "--dangerously-skip-permissions",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return (proc.returncode == 0, proc.stdout or "", proc.stderr or "")
    except subprocess.TimeoutExpired:
        return False, "", f"Claude Code timed out after {timeout}s"
    except Exception as exc:
        return False, "", f"Claude Code failed: {exc}"


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _resolve_repo_full_name(repo: str | None) -> str | None:
    """Map 'self' to the bot's own repo; return None if unconfigured."""
    if not repo or repo.lower() == "self":
        return settings.github_repo or None
    return repo


# --- claude_code (existing repo) ----------------------------------------

async def _claude_code(args: dict, context: "Context") -> str:
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return json.dumps({"error": "prompt is required"})

    if not _has_claude_cli():
        return json.dumps({"error": "Claude Code CLI not installed in container"})

    full_name = _resolve_repo_full_name(args.get("repo"))
    if not full_name:
        return json.dumps({"error": "no repo specified and GITHUB_REPO not set"})

    clone_url = _authed_clone_url(full_name)
    if not clone_url:
        return json.dumps({"error": "GITHUB_TOKEN required to clone/push"})

    timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)
    timeout = max(60, min(MAX_TIMEOUT, timeout))
    base_branch = (args.get("base_branch") or "main").strip()
    via_pr = bool(args.get("via_pr", False))  # default: direct push to base_branch

    workdir = Path(tempfile.mkdtemp(prefix="omnime-claude-"))
    try:
        # Clone.
        cl = _git(["clone", "--depth", "50", clone_url, str(workdir)], cwd=workdir.parent)
        if cl.returncode != 0:
            return json.dumps({"error": f"git clone failed: {cl.stderr[:500]}"})

        co = _git(["checkout", base_branch], cwd=workdir)
        if co.returncode != 0:
            logger.warning("checkout %s: %s", base_branch, co.stderr)

        logger.info(
            "claude_code: repo=%s base=%s via_pr=%s prompt=%s",
            full_name, base_branch, via_pr, prompt[:80],
        )
        ok, stdout, stderr = _run_claude(prompt, workdir, timeout)
        log_excerpt = (stdout or stderr or "")[:3000]
        if not ok:
            return json.dumps({
                "status": "claude_failed",
                "stderr": stderr[:1500],
                "stdout": stdout[:1500],
            })

        diff = _git(["diff", "--stat"], cwd=workdir)
        full_diff = _git(["diff"], cwd=workdir)
        if not full_diff.stdout.strip():
            return json.dumps({
                "status": "no_changes",
                "claude_output": log_excerpt,
            })

        commit_msg = f"Claude Code: {prompt[:120]}"
        if via_pr:
            # PR flow: feature branch + push + open PR.
            branch_name = (args.get("branch_name") or "").strip() or f"claude-code/{_slugify(prompt)}"
            _git(["checkout", "-b", branch_name], cwd=workdir)
            _git(["add", "-A"], cwd=workdir)
            ci = _git(["commit", "-m", commit_msg], cwd=workdir)
            if ci.returncode != 0:
                return json.dumps({"error": f"git commit failed: {ci.stderr[:500]}"})
            push = _git(["push", "origin", branch_name], cwd=workdir)
            if push.returncode != 0:
                return json.dumps({"error": f"git push failed: {push.stderr[:500]}"})
            client = GitHubClient()
            if not client.enabled:
                return json.dumps({
                    "status": "branch_pushed_no_pr",
                    "branch": branch_name,
                    "claude_output": log_excerpt,
                })
            pr = client.open_pr(
                title=f"Claude Code: {prompt[:80]}",
                body=(
                    f"Generated by Claude Code from prompt:\n\n> {prompt}\n\n"
                    f"---\n\n**Diffstat:**\n```\n{diff.stdout}\n```\n\n"
                    f"<details><summary>Claude Code output (truncated)</summary>\n\n"
                    f"```\n{log_excerpt}\n```\n</details>"
                ),
                head=branch_name,
                base=base_branch,
                repo=full_name,
            )
            return json.dumps({
                "status": "pr_opened",
                "repo": full_name,
                "branch": branch_name,
                "pr_url": pr.get("url"),
                "diffstat": diff.stdout[:1000],
            }, ensure_ascii=False)

        # Direct-push flow: commit on base_branch, push.
        _git(["add", "-A"], cwd=workdir)
        ci = _git(["commit", "-m", commit_msg], cwd=workdir)
        if ci.returncode != 0:
            return json.dumps({"error": f"git commit failed: {ci.stderr[:500]}"})
        push = _git(["push", "origin", base_branch], cwd=workdir)
        if push.returncode != 0:
            return json.dumps({"error": f"git push failed: {push.stderr[:500]}"})
        return json.dumps({
            "status": "pushed_to_main",
            "repo": full_name,
            "branch": base_branch,
            "diffstat": diff.stdout[:1000],
            "commit_msg": commit_msg,
        }, ensure_ascii=False)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


CLAUDE_CODE = Tool(
    name="claude_code",
    description=(
        "DEFAULT TOOL FOR CODE WORK — clones a GitHub repo into a fresh "
        "/tmp dir, runs Claude Code with your prompt (multi-file aware, "
        "navigates the repo, runs tests, iterates). If the prompt "
        "produces a diff, BY DEFAULT the change is committed and "
        "**pushed directly to base_branch** (typically main) — no PR "
        "step. The repo's auto-deploy then ships the change. If the "
        "prompt is read-only, returns analysis text with no commit. "
        "Use this for ANY substantial code task: deep-dive, debug, "
        "refactor, fix, add feature, audit. Uses the user's Pro/Max "
        "subscription = free within plan limits.\n\n"
        "WRITE confirmation: when the prompt would change files, FIRST "
        "diagnose + propose to the user and WAIT for an explicit "
        "go-ahead ('sí', 'hazlo', 'arréglalo'). Read-only prompts "
        "don't need confirmation.\n\n"
        "PR mode: pass via_pr=true ONLY if the user asks 'open a PR' / "
        "'no quiero push directo' / requires review. Default is direct "
        "push.\n\n"
        "Reserve bot_read_source / bot_grep_source / github_read_file "
        "for one-line trivial lookups only.\n\n"
        "``repo='self'`` (default) targets the bot's own repo; any "
        "'owner/name' targets a different repo the GitHub token can "
        "access."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "What Claude Code should do — be concrete: 'fix the "
                    "/files pagination bug, add a test', 'refactor "
                    "browser_agent.py for clarity, behaviour identical, "
                    "tests pass'."
                ),
            },
            "repo": {
                "type": "string",
                "description": "'self' (default) or 'owner/name' for another repo.",
            },
            "base_branch": {"type": "string", "default": "main"},
            "via_pr": {
                "type": "boolean",
                "default": False,
                "description": "Default false = direct push to base_branch. Set true ONLY if the user explicitly wants a PR for review.",
            },
            "branch_name": {"type": "string", "description": "Only used when via_pr=true. Optional override; auto-slugified from prompt otherwise."},
            "timeout": {"type": "integer", "default": DEFAULT_TIMEOUT, "minimum": 60, "maximum": MAX_TIMEOUT},
        },
        "required": ["prompt"],
    },
    run=_claude_code,
)


# --- claude_code_new_project --------------------------------------------

async def _claude_code_new_project(args: dict, context: "Context") -> str:
    name = (args.get("name") or "").strip()
    description = (args.get("description") or "").strip()
    prompt = (args.get("prompt") or "").strip()
    private = bool(args.get("private", True))

    if not name or not prompt:
        return json.dumps({"error": "name and prompt are required"})
    if not _has_claude_cli():
        return json.dumps({"error": "Claude Code CLI not installed"})

    client = GitHubClient()
    if not client.enabled:
        return json.dumps({"error": "GitHub not configured"})

    # 1) Create the repo.
    try:
        created = client.create_repo(
            name=name, description=description, private=private, auto_init=True,
        )
    except Exception as exc:
        return json.dumps({"error": f"create_repo failed: {exc}"})
    full_name = created["full_name"]

    # 2) Clone, run Claude Code, push back.
    clone_url = _authed_clone_url(full_name)
    timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)
    timeout = max(60, min(MAX_TIMEOUT, timeout))
    workdir = Path(tempfile.mkdtemp(prefix="omnime-claude-new-"))
    try:
        cl = _git(["clone", clone_url, str(workdir)], cwd=workdir.parent)
        if cl.returncode != 0:
            return json.dumps({
                "status": "repo_created_clone_failed",
                "repo": full_name, "url": created["url"],
                "error": cl.stderr[:500],
            })

        scaffold_prompt = (
            f"This is a brand-new GitHub repo named '{name}'. {description}\n\n"
            f"Scaffold it according to this spec:\n\n{prompt}\n\n"
            "Create whatever files make sense (README, source, tests, "
            ".gitignore, dependency manifest). Be opinionated and complete."
        )
        ok, stdout, stderr = _run_claude(scaffold_prompt, workdir, timeout)
        if not ok:
            return json.dumps({
                "status": "scaffold_failed",
                "repo": full_name, "url": created["url"],
                "stderr": stderr[:1500],
            })

        _git(["add", "-A"], cwd=workdir)
        ci = _git(["commit", "-m", f"Claude Code: scaffold {name}"], cwd=workdir)
        if ci.returncode != 0:
            return json.dumps({
                "status": "scaffold_no_changes",
                "repo": full_name, "url": created["url"],
                "claude_output": (stdout or "")[:1500],
            })
        push = _git(["push", "origin", "main"], cwd=workdir)
        if push.returncode != 0:
            return json.dumps({
                "status": "push_failed",
                "repo": full_name, "url": created["url"],
                "error": push.stderr[:500],
            })
        return json.dumps({
            "status": "created_and_scaffolded",
            "repo": full_name,
            "url": created["url"],
            "claude_output": (stdout or "")[:1500],
        }, ensure_ascii=False)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


CLAUDE_CODE_NEW_PROJECT = Tool(
    name="claude_code_new_project",
    description=(
        "Bootstrap a brand-new GitHub repo and scaffold it with Claude "
        "Code. Creates a private repo by default, clones it, runs Claude "
        "Code with a scaffolding prompt, commits, pushes. ONLY call when "
        "the user EXPLICITLY says 'crea un proyecto nuevo' / 'start a "
        "new project' AND describes what they want. Returns the new "
        "repo URL."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Repo name (kebab-case ideal)."},
            "description": {"type": "string"},
            "prompt": {
                "type": "string",
                "description": "What the project does + any tech preferences.",
            },
            "private": {"type": "boolean", "default": True},
            "timeout": {"type": "integer", "default": DEFAULT_TIMEOUT, "minimum": 60, "maximum": MAX_TIMEOUT},
        },
        "required": ["name", "prompt"],
    },
    run=_claude_code_new_project,
)


async def _claude_code_analyze(args: dict, context: "Context") -> str:
    """Read-only analysis of a stored file via Claude Code. Free under the
    user's Pro/Max subscription — better than feeding huge PDFs/CSVs/code
    files into the agentic loop's API context (which charges per token)."""
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return json.dumps({"error": "prompt is required"})
    if not _has_claude_cli():
        return json.dumps({"error": "Claude Code CLI not installed"})

    user_id = int(getattr(context, "user_id", 0) or 0)
    fid = args.get("file_record_id")
    filename = (args.get("filename") or "").strip()

    # Resolve to a local Path. Reuses the same logic as drive_tools/chat_tools.
    src_path: Path | None = None
    name: str | None = None
    if fid is not None:
        from sqlalchemy import select
        from src.memory import models as m
        from src.memory.db import session_scope

        with session_scope() as s:
            row = s.execute(
                select(m.FileRecord)
                .where(m.FileRecord.id == int(fid))
                .where(m.FileRecord.user_id == user_id)
            ).scalar_one_or_none()
            if row is None:
                return json.dumps({"error": f"file_record_id {fid} not found"})
            name = row.filename
            src_path = Path(settings.uploads_dir) / (name or "")
    elif filename:
        name = filename
        src_path = Path(settings.uploads_dir) / filename
    else:
        return json.dumps({"error": "either file_record_id or filename is required"})

    if src_path is None or not src_path.exists():
        return json.dumps({"error": f"file not found on disk: {name}"})

    timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)
    timeout = max(60, min(MAX_TIMEOUT, timeout))

    workdir = Path(tempfile.mkdtemp(prefix="omnime-analyze-"))
    try:
        target = workdir / src_path.name
        shutil.copy(src_path, target)
        size_kb = target.stat().st_size // 1024

        full_prompt = (
            f"You are analysing the file `./{src_path.name}` in this "
            f"directory. Read it fully (it's {size_kb} KB). User's "
            f"question / instruction:\n\n{prompt}\n\n"
            "Reply with the analysis. Be concrete: cite specific "
            "sections / rows / lines when relevant. Match the user's "
            "language."
        )

        logger.info(
            "claude_code_analyze: file=%s size=%dKB prompt=%s",
            src_path.name, size_kb, prompt[:80],
        )
        ok, stdout, stderr = _run_claude(full_prompt, workdir, timeout)
        if not ok:
            return json.dumps({
                "status": "claude_failed",
                "filename": src_path.name,
                "stderr": (stderr or "")[:1500],
            })

        return json.dumps({
            "status": "analyzed",
            "filename": src_path.name,
            "size_kb": size_kb,
            "analysis": (stdout or "")[:10000],
        }, ensure_ascii=False)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


CLAUDE_CODE_ANALYZE = Tool(
    name="claude_code_analyze",
    description=(
        "Deep-dive analysis of an uploaded file using Claude Code (free "
        "under the user's Pro/Max subscription). PREFERRED over feeding "
        "the file into the regular loop when:\n"
        "- The file is large (>50KB / >30 pages PDF / >5k row CSV / "
        "  >500 line code)\n"
        "- The user wants thorough, multi-pass analysis\n"
        "- The file is structured data (CSV / JSON / code) where "
        "  Claude Code's tools (Grep, Read, Bash) help.\n\n"
        "Resolve the file via file_record_id (preferred — from "
        "files_list / files_search) or filename. Returns the analysis "
        "text — no commit, no push, no PR. For SHORT files where the "
        "answer fits in one page, the regular loop is fine and faster."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_record_id": {"type": "integer", "description": "Preferred: id from files_list/files_search."},
            "filename": {"type": "string", "description": "Alternative: filename inside data/uploads/."},
            "prompt": {
                "type": "string",
                "description": "What to analyse. Be specific: 'extract all dates and amounts', 'summarise chapter by chapter', 'find inconsistencies', etc.",
            },
            "timeout": {"type": "integer", "default": DEFAULT_TIMEOUT, "minimum": 60, "maximum": MAX_TIMEOUT},
        },
        "required": ["prompt"],
    },
    run=_claude_code_analyze,
)


def build_claude_code_tools() -> list[Tool]:
    if not _has_claude_cli():
        return []
    return [CLAUDE_CODE, CLAUDE_CODE_NEW_PROJECT, CLAUDE_CODE_ANALYZE]
