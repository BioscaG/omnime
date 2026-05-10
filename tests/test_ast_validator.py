"""AST allowlist for self-evolution candidates."""
from __future__ import annotations

import textwrap

import pytest

from src.evolution.ast_validator import validate


def _wrap(body: str) -> str:
    return textwrap.dedent(body)


def test_allows_minimal_skill():
    code = _wrap("""
        from src.skills.base import BaseSkill, SkillResponse


        class Hello(BaseSkill):
            name = "hello"
            description = "say hi"
            triggers = ["hi"]

            def __init__(self, llm, memory):
                self.llm = llm
                self.memory = memory

            def can_handle(self, message, intent=None):
                return 0.5

            async def execute(self, message, context):
                return SkillResponse(text="hi")
    """)
    res = validate(code)
    assert res.ok, res.reasons


def test_rejects_os_import():
    code = _wrap("""
        import os
        from src.skills.base import BaseSkill
        class X(BaseSkill):
            pass
    """)
    res = validate(code)
    assert not res.ok
    assert any("os" in r for r in res.reasons)


def test_rejects_subprocess_import():
    code = _wrap("""
        import subprocess
        from src.skills.base import BaseSkill
        class X(BaseSkill):
            pass
    """)
    res = validate(code)
    assert not res.ok


def test_rejects_eval_call():
    code = _wrap("""
        from src.skills.base import BaseSkill
        class X(BaseSkill):
            def go(self):
                return eval("1+1")
    """)
    res = validate(code)
    assert not res.ok
    assert any("eval" in r for r in res.reasons)


def test_rejects_dunder_access():
    code = _wrap("""
        from src.skills.base import BaseSkill
        class X(BaseSkill):
            def go(self):
                return type(1).__mro__
    """)
    res = validate(code)
    assert not res.ok


def test_rejects_module_without_skill_class():
    code = "import json\n"
    res = validate(code)
    assert not res.ok


def test_syntax_error_returned():
    res = validate("def broken(:\n")
    assert not res.ok
    assert "Syntax" in res.first_reason
