"""Render a Mermaid knowledge graph from the structured store.

The graph nodes are the user, projects, contacts and skills. Edges represent
relationships extracted from the data: who collaborated on what, which tech a
project uses, where a contact works.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.memory.db import session_scope
from src.memory.structured import StructuredStore
from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


class KnowledgeGraphSkill(BaseSkill):
    name = "knowledge_graph"
    description = "Render a Mermaid graph of projects, contacts, skills and tech."
    triggers = ["/graph", "/knowledge_graph", "knowledge graph", "show me the graph"]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        if m.startswith("/graph") or m.startswith("/knowledge_graph"):
            return 0.95
        if "knowledge graph" in m:
            return 0.85
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        focus = self._extract_focus(message)
        with session_scope() as s:
            store = StructuredStore(s)
            projects = store.list_projects(context.user_id)
            contacts = store.list_contacts(context.user_id)
            skills = store.list_skills(context.user_id)

        if focus:
            projects = [p for p in projects if focus.lower() in (p.name or "").lower()
                        or focus.lower() in (p.description or "").lower()]
            contacts = [c for c in contacts if focus.lower() in (c.name or "").lower()
                        or focus.lower() in (c.organization or "").lower()]
            skills = [k for k in skills if focus.lower() in (k.name or "").lower()]

        if not (projects or contacts or skills):
            return SkillResponse(text="Nothing to graph yet.")

        lines: list[str] = ["graph TD", '    USER(("👤 you"))']
        for p in projects[:25]:
            pid = self._id(f"P{p.id}")
            lines.append(f'    {pid}["📁 {self._escape(p.name)}"]')
            lines.append(f"    USER --> {pid}")
            for tech in (p.technologies or [])[:6]:
                tid = self._id(f"T_{tech}")
                lines.append(f'    {tid}(("⚙️ {self._escape(tech)}"))')
                lines.append(f"    {pid} --> {tid}")
        for c in contacts[:20]:
            cid = self._id(f"C{c.id}")
            label = f"👥 {self._escape(c.name)}"
            if c.organization:
                label += f" ({self._escape(c.organization)})"
            lines.append(f'    {cid}["{label}"]')
            lines.append(f"    USER -.-> {cid}")
        for sk in skills[:25]:
            sid = self._id(f"S{sk.id}")
            level = sk.proficiency or "?"
            lines.append(f'    {sid}["🛠 {self._escape(sk.name)} · {level}"]')
            lines.append(f"    USER --> {sid}")

        graph = "\n".join(lines)
        return SkillResponse(
            text=f"```mermaid\n{graph}\n```",
            metadata={"focus": focus, "nodes": len(projects) + len(contacts) + len(skills)},
        )

    @staticmethod
    def _extract_focus(message: str) -> str | None:
        m = message.strip()
        for tok in ("/graph", "/knowledge_graph"):
            if m.lower().startswith(tok):
                rest = m[len(tok):].strip()
                return rest or None
        return None

    @staticmethod
    def _id(raw: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]+", "_", raw)[:40] or "N"

    @staticmethod
    def _escape(value: str | None) -> str:
        return (value or "").replace('"', "'").replace("\n", " ")[:60]
