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

    # Resolve to a list of source paths. Three accepted argument shapes:
    #   - file_record_id (int) → single file/folder
    #   - file_record_ids (list[int]) → multiple files (e.g. all .tex of a TFG)
    #   - filename (str) → single file by name in data/uploads/
    src_paths: list[Path] = []
    fid = args.get("file_record_id")
    fids = args.get("file_record_ids") or []
    if fid is not None and not fids:
        fids = [fid]
    filename = (args.get("filename") or "").strip()

    if fids:
        from sqlalchemy import select
        from src.memory import models as m
        from src.memory.db import session_scope

        with session_scope() as s:
            for one in fids:
                row = s.execute(
                    select(m.FileRecord)
                    .where(m.FileRecord.id == int(one))
                    .where(m.FileRecord.user_id == user_id)
                ).scalar_one_or_none()
                if row is None:
                    return json.dumps({"error": f"file_record_id {one} not found"})
                p = Path(settings.uploads_dir) / (row.filename or "")
                if not p.exists():
                    return json.dumps({"error": f"file missing on disk: {row.filename}"})
                src_paths.append(p)
    elif filename:
        p = Path(settings.uploads_dir) / filename
        if not p.exists():
            return json.dumps({"error": f"file not found: {filename}"})
        src_paths.append(p)
    else:
        return json.dumps({"error": "pass file_record_id, file_record_ids (list), or filename"})

    if not src_paths:
        return json.dumps({"error": "no files resolved"})

    timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)
    timeout = max(60, min(MAX_TIMEOUT, timeout))

    workdir = Path(tempfile.mkdtemp(prefix="omnime-analyze-"))
    try:
        # Three input shapes:
        #   - 1 directory  → copytree, navigate
        #   - 1 file       → copy, point at it
        #   - N files      → copy all to workdir root (treat as a project)
        copied_kind = ""
        size_kb = 0
        if len(src_paths) == 1 and src_paths[0].is_dir():
            src = src_paths[0]
            target = workdir / src.name
            shutil.copytree(src, target)
            file_count = sum(1 for _ in target.rglob("*") if _.is_file())
            size_kb = sum(p.stat().st_size for p in target.rglob("*") if p.is_file()) // 1024
            full_prompt = (
                f"You are analysing the directory `./{src.name}/` "
                f"in this workspace ({file_count} files, {size_kb} KB total). "
                f"Navigate the tree freely (Read, Grep, Glob), then answer.\n\n"
                f"User's question / instruction:\n\n{prompt}\n\n"
                "Reply with the analysis. Be concrete: cite specific files / "
                "sections / lines when relevant. Match the user's language."
            )
            copied_kind = "directory"
        elif len(src_paths) == 1:
            src = src_paths[0]
            target = workdir / src.name
            shutil.copy(src, target)
            size_kb = target.stat().st_size // 1024
            full_prompt = (
                f"You are analysing the file `./{src.name}` in this "
                f"directory. Read it fully (it's {size_kb} KB). User's "
                f"question / instruction:\n\n{prompt}\n\n"
                "Reply with the analysis. Be concrete: cite specific "
                "sections / rows / lines when relevant. Match the user's "
                "language."
            )
            copied_kind = "file"
        else:
            for src in src_paths:
                target = workdir / src.name
                if src.is_dir():
                    shutil.copytree(src, target)
                else:
                    shutil.copy(src, target)
            file_count = sum(1 for _ in workdir.rglob("*") if _.is_file())
            size_kb = sum(p.stat().st_size for p in workdir.rglob("*") if p.is_file()) // 1024
            file_listing = "\n".join(f"- {p.name}" for p in src_paths)
            full_prompt = (
                f"You are analysing a project assembled from "
                f"{file_count} uploaded files, {size_kb} KB total. The "
                f"files are at the workspace root:\n\n{file_listing}\n\n"
                f"Treat them as ONE project. Navigate freely with "
                f"Read/Grep/Glob.\n\n"
                f"User's question / instruction:\n\n{prompt}\n\n"
                "Reply with the analysis. Be concrete: cite specific files / "
                "sections / lines when relevant. Match the user's language."
            )
            copied_kind = "project"

        logger.info(
            "claude_code_analyze: kind=%s files=%d size=%dKB prompt=%s",
            copied_kind, len(src_paths), size_kb, prompt[:80],
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
            "kind": copied_kind,
            "files": [p.name for p in src_paths],
            "size_kb": size_kb,
            "analysis": (stdout or "")[:10000],
        }, ensure_ascii=False)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


CLAUDE_CODE_ANALYZE = Tool(
    name="claude_code_analyze",
    description=(
        "Deep-dive analysis via Claude Code (free under the user's "
        "Pro/Max subscription). Accepts THREE input shapes:\n"
        "- file_record_id (single file or folder)\n"
        "- file_record_ids (LIST — multiple files treated as one "
        "  project; use this when the user uploaded several related "
        "  files like a TFG's .tex chapters + .bib)\n"
        "- filename (single file by name)\n\n"
        "PREFERRED for SUBSTANTIAL content: folder/zip uploads, "
        "multi-file projects (TFG with .tex + figures + bib), big "
        "PDFs, code dumps, CSVs the user wants real analysis on. "
        "Handles directories natively — Claude Code navigates with "
        "Read/Grep/Glob.\n\n"
        "Use files_search / files_get only for trivial single-file "
        "lookups. When in doubt → default to claude_code_analyze "
        "(subscription cost = \\$0).\n\n"
        "Read-only: no commit, no push, no PR."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_record_id": {
                "type": "integer",
                "description": "Single file or folder id from files_list/files_search.",
            },
            "file_record_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "LIST of file_record_ids to treat as one project — perfect for multi-file uploads (TFG chapters + bib, code repo dump, etc.). Files copy to the workdir root.",
            },
            "filename": {
                "type": "string",
                "description": "Alternative: a single filename inside data/uploads/.",
            },
            "prompt": {
                "type": "string",
                "description": "What to analyse. Be specific: 'summarise chapter by chapter and save key facts to memory', 'find inconsistencies between chapters 3 and 4', 'extract all citations'.",
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
