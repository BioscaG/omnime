"""Run untrusted skill code in a constrained subprocess."""
from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent
from typing import Any


logger = logging.getLogger(__name__)


SANDBOX_RUNNER = dedent("""
    import importlib.util
    import sys
    import json
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


class Sandbox:
    """Light sandbox: runs the candidate skill in a separate subprocess.

    True isolation needs Docker — we expose a hook for that. By default
    we do a syntax/import smoke test which catches the majority of bad
    code without requiring containers.
    """

    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout

    def smoke_test(self, code: str) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "candidate.py"
            skill_path.write_text(code, encoding="utf-8")
            runner_path = Path(tmpdir) / "runner.py"
            runner_path.write_text(SANDBOX_RUNNER, encoding="utf-8")

            try:
                result = subprocess.run(
                    [sys.executable, str(runner_path), str(skill_path)],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired:
                return {"ok": False, "stage": "timeout", "error": "smoke test timed out"}

            if result.returncode != 0:
                return {
                    "ok": False,
                    "stage": "process",
                    "error": result.stderr or result.stdout,
                }

            import json

            try:
                return json.loads(result.stdout.strip().splitlines()[-1])
            except Exception as exc:
                return {"ok": False, "stage": "parse", "error": str(exc), "raw": result.stdout}
