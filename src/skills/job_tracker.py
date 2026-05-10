"""Job opportunities tracker: list, filter and update job pipeline."""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from src.memory.db import session_scope
from src.memory.structured import StructuredStore
from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


VALID_STATUSES = {
    "discovered", "applied", "interviewing", "offer", "rejected", "withdrawn",
}


class JobTrackerSkill(BaseSkill):
    name = "job_tracker"
    description = "Track and update job opportunities (status, next steps)."
    triggers = ["/jobs", "/jobtracker", "job tracker", "job pipeline", "job applications"]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower().strip()
        if m.startswith("/jobs") or m.startswith("/jobtracker"):
            return 0.95
        if "job tracker" in m or "job pipeline" in m:
            return 0.85
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        args = self._parse(message)
        action = args.get("action")
        if action == "list":
            return self._list(context, args.get("status"))
        if action == "set":
            return self._set_status(context, args["company"], args["role"], args["status"])
        return self._list(context, None)

    @staticmethod
    def _parse(message: str) -> dict:
        text = message.strip()
        for tok in ("/jobs", "/jobtracker"):
            if text.lower().startswith(tok):
                text = text[len(tok):].strip()
        if not text:
            return {"action": "list"}
        if text.lower().startswith("status "):
            # `status applied`
            return {"action": "list", "status": text.split(maxsplit=1)[1].strip()}
        if " set " in (" " + text.lower()):
            # set "Company" "Role" applied
            try:
                _, company, role, status = text.split('"')[:5][:4] if '"' in text else (None,) * 4
            except Exception:
                pass
        # Generic: `Company | Role | status`
        parts = [p.strip() for p in text.split("|")]
        if len(parts) == 3:
            return {"action": "set", "company": parts[0], "role": parts[1], "status": parts[2]}
        return {"action": "list"}

    def _list(self, context: "Context", status: str | None) -> SkillResponse:
        with session_scope() as s:
            jobs = StructuredStore(s).list_job_opportunities(context.user_id, status=status)
            if not jobs:
                return SkillResponse(text="No job opportunities tracked yet.")
            lines = [f"📋 Job pipeline ({status or 'all'}):"]
            for j in jobs:
                next_step = f" • next: {j.next_step_at}" if j.next_step_at else ""
                lines.append(
                    f"• **{j.role}** @ {j.company} — `{j.status}`{next_step}"
                )
            return SkillResponse(text="\n".join(lines))

    def _set_status(self, context: "Context", company: str, role: str, status: str) -> SkillResponse:
        if status.lower() not in VALID_STATUSES:
            return SkillResponse(
                text=f"Status must be one of: {', '.join(sorted(VALID_STATUSES))}"
            )
        applied_at = date.today() if status.lower() == "applied" else None
        with session_scope() as s:
            store = StructuredStore(s)
            j = store.upsert_job_opportunity(
                user_id=context.user_id,
                company=company,
                role=role,
                status=status.lower(),
                applied_at=applied_at,
            )
            store.add_audit(
                context.user_id, "update", "job_opportunity", j.id,
                {"company": company, "role": role, "status": status},
            )
        return SkillResponse(text=f"Updated: {role} @ {company} → {status}")
