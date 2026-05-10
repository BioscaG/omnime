"""Run untrusted skill code in a constrained subprocess.

Layered defence:
  1. AST allowlist (`ast_validator`) — fast static reject of obvious abuse.
  2. Subprocess execution with the parent environment stripped (no API keys,
     no DB credentials, no Telegram token reachable from the candidate).
  3. Optional Docker container with --network=none and read-only filesystem
     when Docker is available — gates everything behind kernel-level isolation.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent
from typing import Any

from src.evolution.ast_validator import validate


logger = logging.getLogger(__name__)


SANDBOX_RUNNER = dedent("""
    import importlib.util
    import json
    import sys
    import traceback


    def main(skill_path):
        spec = importlib.util.spec_from_file_location("candidate_skill", skill_path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception:
            return {"ok": False, "stage": "import", "error": traceback.format_exc()}

        skill_classes = []
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and obj.__module__ == "candidate_skill":
                bases = [b.__name__ for b in obj.__mro__]
                if "BaseSkill" in bases:
                    skill_classes.append(name)
        if not skill_classes:
            return {"ok": False, "stage": "discovery", "error": "no BaseSkill subclass found"}

        return {"ok": True, "stage": "import", "classes": skill_classes}


    if __name__ == "__main__":
        result = main(sys.argv[1])
        print(json.dumps(result))
""")


# Env vars the candidate is *allowed* to inherit (basically nothing risky).
SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "PYTHONIOENCODING")


def _safe_env() -> dict[str, str]:
    """Return a stripped env: only safe vars plus PYTHONPATH limited to the
    project root so the candidate can do ``from src.skills.base import …``
    without inheriting application secrets."""
    env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}
    project_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = project_root
    return env


class Sandbox:
    """Run a candidate skill behind AST validation + isolated subprocess.

    Parameters
    ----------
    timeout : int
        Wall-clock seconds before the subprocess is killed.
    use_docker : bool
        When True (and the docker CLI is on PATH) the candidate runs inside a
        scratch container with --network=none. Otherwise falls back to a
        subprocess with stripped env.
    docker_image : str
        Image to run the candidate in.
    memory_limit : str
        Docker --memory value.
    """

    def __init__(
        self,
        timeout: int = 15,
        use_docker: bool = True,
        docker_image: str = "python:3.12-slim",
        memory_limit: str = "256m",
    ) -> None:
        self.timeout = timeout
        self.use_docker = use_docker and shutil.which("docker") is not None
        self.docker_image = docker_image
        self.memory_limit = memory_limit

    # --- Public ---------------------------------------------------------
    def smoke_test(self, code: str) -> dict[str, Any]:
        """Validate AST then run the candidate in isolation."""
        report = validate(code)
        if not report.ok:
            return {"ok": False, "stage": "ast", "error": "; ".join(report.reasons)}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            skill_path = tmp / "candidate.py"
            skill_path.write_text(code, encoding="utf-8")
            runner_path = tmp / "runner.py"
            runner_path.write_text(SANDBOX_RUNNER, encoding="utf-8")

            if self.use_docker:
                return self._run_in_docker(tmp, runner_path, skill_path)
            return self._run_in_subprocess(runner_path, skill_path)

    # --- Backends -------------------------------------------------------
    def _run_in_subprocess(self, runner_path: Path, skill_path: Path) -> dict[str, Any]:
        try:
            result = subprocess.run(
                [sys.executable, str(runner_path), str(skill_path)],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=_safe_env(),
                cwd=str(runner_path.parent),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "stage": "timeout", "error": "smoke test timed out"}

        return self._parse_result(result)

    def _run_in_docker(self, tmpdir: Path, runner_path: Path, skill_path: Path) -> dict[str, Any]:
        cmd = [
            "docker", "run", "--rm",
            "--network=none",
            "--read-only",
            "--tmpfs", "/tmp",
            "--memory", self.memory_limit,
            "--cpus", "0.5",
            "--cap-drop=ALL",
            "--security-opt", "no-new-privileges",
            "--user", "65534:65534",  # nobody:nogroup
            "-v", f"{tmpdir}:/sandbox:ro",
            "-w", "/sandbox",
            self.docker_image,
            "python", "/sandbox/runner.py", "/sandbox/candidate.py",
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout + 10,
                env=_safe_env(),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "stage": "timeout", "error": "Docker sandbox timed out"}
        except FileNotFoundError:
            logger.warning("Docker not available, falling back to subprocess")
            return self._run_in_subprocess(runner_path, skill_path)

        return self._parse_result(result)

    @staticmethod
    def _parse_result(result: subprocess.CompletedProcess) -> dict[str, Any]:
        if result.returncode != 0:
            return {
                "ok": False,
                "stage": "process",
                "error": (result.stderr or result.stdout)[:2000],
            }
        try:
            last_line = result.stdout.strip().splitlines()[-1]
            return json.loads(last_line)
        except Exception as exc:
            return {"ok": False, "stage": "parse", "error": str(exc), "raw": result.stdout[:2000]}
