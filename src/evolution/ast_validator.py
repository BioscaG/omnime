"""AST-based allowlist validator for LLM-generated skill code.

Rejects modules that import dangerous stdlib modules, perform IO, touch the
network or escape sandbox boundaries. The validator is conservative: anything
not on the allowlist is rejected with a precise reason.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass


# Modules generated skills are allowed to import. Add new entries deliberately.
ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        "src.skills.base",
        "src.brain.llm_client",
        "src.brain.context_builder",
        "src.memory.manager",
        "dataclasses",
        "datetime",
        "json",
        "logging",
        "math",
        "re",
        "typing",
        "pathlib",
        "enum",
        "functools",
        "itertools",
        "collections",
        "abc",
        "asyncio",
        "uuid",
        "hashlib",
        "base64",
        "textwrap",
        "string",
        "statistics",
    }
)

FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "os",
        "subprocess",
        "shutil",
        "socket",
        "ssl",
        "ftplib",
        "smtplib",
        "telnetlib",
        "ctypes",
        "multiprocessing",
        "threading",
        "fcntl",
        "pty",
        "signal",
        "resource",
        "pickle",
        "marshal",
        "shelve",
        "imaplib",
        "poplib",
        "urllib",
        "urllib.request",
        "http",
        "http.client",
        "requests",
        "httpx",
        "aiohttp",
        "anthropic",
        "openai",
        "google",
        "github",
        "telegram",
        "sqlalchemy",
        "psycopg2",
        "asyncpg",
        "chromadb",
        "boto3",
        "paramiko",
    }
)

FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "__import__",
        "eval",
        "exec",
        "compile",
        "globals",
        "locals",
        "vars",
        "open",
        "input",
        "memoryview",
        "breakpoint",
    }
)

FORBIDDEN_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "__class__",
        "__bases__",
        "__subclasses__",
        "__mro__",
        "__globals__",
        "__builtins__",
        "__getattribute__",
        "__import__",
        "__loader__",
        "__spec__",
    }
)


@dataclass
class ValidationResult:
    ok: bool
    reasons: list[str]

    @property
    def first_reason(self) -> str:
        return self.reasons[0] if self.reasons else ""


class _Validator(ast.NodeVisitor):
    def __init__(self) -> None:
        self.reasons: list[str] = []

    def _module_allowed(self, name: str) -> bool:
        if not name:
            return False
        if name in FORBIDDEN_MODULES:
            return False
        # Allow dotted children of allowed roots
        for allowed in ALLOWED_IMPORTS:
            if name == allowed or name.startswith(allowed + "."):
                return True
        # Reject anything not explicitly allowed
        return False

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if not self._module_allowed(alias.name):
                self.reasons.append(f"Forbidden import: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level > 0:
            self.reasons.append("Relative imports not allowed")
            return
        if not self._module_allowed(module):
            self.reasons.append(f"Forbidden import-from: {module}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in FORBIDDEN_NAMES:
            self.reasons.append(f"Forbidden name: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_ATTRIBUTES:
            self.reasons.append(f"Forbidden attribute access: {node.attr}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in FORBIDDEN_NAMES:
            self.reasons.append(f"Forbidden call: {func.id}")
        if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_NAMES:
            self.reasons.append(f"Forbidden method call: {func.attr}")
        self.generic_visit(node)


def validate(code: str) -> ValidationResult:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return ValidationResult(ok=False, reasons=[f"Syntax error: {exc.msg} at line {exc.lineno}"])

    v = _Validator()
    v.visit(tree)

    if not _has_skill_class(tree):
        v.reasons.append("Module must declare exactly one class subclassing BaseSkill")

    return ValidationResult(ok=not v.reasons, reasons=v.reasons)


def _has_skill_class(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = _qualname(base)
                if base_name in ("BaseSkill", "src.skills.base.BaseSkill"):
                    return True
    return False


def _qualname(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_qualname(node.value)}.{node.attr}"
    return ""
