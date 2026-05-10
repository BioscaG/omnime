"""Sandbox runs a candidate skill in a stripped subprocess."""
from __future__ import annotations

import textwrap

import pytest

from src.evolution.sandbox import Sandbox


GOOD_SKILL = textwrap.dedent("""
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


BAD_SKILL = "import os\nfrom src.skills.base import BaseSkill\nclass X(BaseSkill):\n    pass\n"


def test_smoke_test_passes_for_valid_skill():
    sb = Sandbox(use_docker=False, timeout=20)
    res = sb.smoke_test(GOOD_SKILL)
    assert res["ok"], res
    assert "Hello" in res.get("classes", [])


def test_smoke_test_fails_ast_for_bad_imports():
    sb = Sandbox(use_docker=False)
    res = sb.smoke_test(BAD_SKILL)
    assert not res["ok"]
    assert res["stage"] == "ast"
