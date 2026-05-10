"""Code-execution primitive — runs Python in a restricted subprocess.

For 'cuánto es 15% de 847', 'convierte 230£ a euros', 'analiza este CSV'.
Without this the model hallucinates numeric answers — terrible for an
assistant that's supposed to be useful with money/data.

Sandbox is a separate Python subprocess with:
- Timeout (10s default, capped at 30s)
- Memory limit (~150MB via RLIMIT_AS)
- stdout + stderr captured and returned to the model
- The container itself is the outer sandbox — single-user bot, contained
  risk — so we accept that the code can read the container's environment
  but it can't break out

Stdlib + numpy/pandas (if installed) are usable. The model writes the
code itself; our job is just to execute it safely and return what it
printed.
"""
from __future__ import annotations

import json
import logging
import resource
import subprocess
import sys
from typing import TYPE_CHECKING

from src.tools import Tool

if TYPE_CHECKING:
    from src.brain.context_builder import Context


logger = logging.getLogger(__name__)


MEMORY_LIMIT_BYTES = 150 * 1024 * 1024  # 150 MB
DEFAULT_TIMEOUT = 10.0
MAX_TIMEOUT = 30.0


def _set_limits() -> None:
    """preexec_fn: cap memory + disable core dumps on the child."""
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass


async def _exec_python(args: dict, context: "Context") -> str:
    code = args.get("code") or ""
    if not code or not isinstance(code, str):
        return json.dumps({"error": "code is required (str)"})
    timeout = float(args.get("timeout") or DEFAULT_TIMEOUT)
    timeout = max(0.5, min(MAX_TIMEOUT, timeout))

    # Wrap the user code so the model can rely on print() output AND
    # the value of the last expression auto-printed (jupyter-like).
    runner = (
        "import sys, traceback\n"
        "try:\n"
        "    _ns = {}\n"
        f"    exec({code!r}, _ns)\n"
        "except Exception:\n"
        "    traceback.print_exc()\n"
        "    sys.exit(1)\n"
    )

    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", runner],
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=_set_limits,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"timeout after {timeout}s", "stdout": "", "stderr": ""})
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    return json.dumps({
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or "")[:8000],
        "stderr": (proc.stderr or "")[:4000],
    }, ensure_ascii=False)


EXEC_PYTHON = Tool(
    name="exec_python",
    description=(
        "Execute Python code in a sandboxed subprocess and return its "
        "stdout/stderr. USE THIS for ANY numerical answer — currency "
        "conversion with rates, percentages, statistics, date math, "
        "parsing CSVs the user has shared, etc. NEVER compute numbers "
        "in your head and claim them — write the Python, run it, report "
        "the result. Stdlib is available; common scientific libs (numpy, "
        "pandas) may be installed. Always use print() to surface results."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source to execute. Use print() to show results.",
            },
            "timeout": {
                "type": "number",
                "default": 10,
                "minimum": 0.5,
                "maximum": 30,
                "description": "Seconds before the run is killed.",
            },
        },
        "required": ["code"],
    },
    run=_exec_python,
)


def build_code_tools() -> list[Tool]:
    return [EXEC_PYTHON]
