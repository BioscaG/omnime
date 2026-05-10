"""Agentic multi-step planner.

Given a user goal, asks Claude to produce a JSON plan of skill calls. Each step
is dispatched through the existing skill registry. Intermediate results are
fed into the next step's context. Bounded by max_steps to avoid runaways.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.brain.context_builder import Context, ContextBuilder
    from src.brain.llm_client import LLMClient
    from src.skills.registry import SkillRegistry


logger = logging.getLogger(__name__)


PLAN_PROMPT = """You are an agentic planner for OMNIME.
Given the user's goal and the available skills, produce a numbered plan as JSON.

Available skills:
{skills}

User goal:
{goal}

Output STRICTLY valid JSON:
{{
  "steps": [
    {{"skill": "<skill_name>", "input": "<message to that skill>",
      "why": "<one short sentence why this step is needed>"}},
    ...
  ],
  "final": "<one-sentence description of how the user's goal is reached>"
}}

Rules:
- Use skills only from the list above.
- 1-5 steps. Smaller is better when sufficient.
- Each step's input should be a complete instruction (not depend on memory of previous steps).
- If the goal needs no skills, return {{"steps": [], "final": "..."}}.
"""


@dataclass
class PlanStep:
    skill: str
    input: str
    why: str = ""
    output: str = ""


@dataclass
class PlanResult:
    final_text: str = ""
    steps: list[PlanStep] = field(default_factory=list)


class Planner:
    def __init__(self, llm: "LLMClient", registry: "SkillRegistry", builder: "ContextBuilder") -> None:
        self.llm = llm
        self.registry = registry
        self.builder = builder

    async def run(self, user_id: int, goal: str, max_steps: int = 4) -> PlanResult:
        skills_block = "\n".join(
            f"- {s.name}: {s.description}" for s in self.registry.list_skills()
        )
        plan_raw = await self.llm.complete(
            prompt=PLAN_PROMPT.format(skills=skills_block, goal=goal),
            system="You produce concise JSON plans, never prose.",
            model_tier="powerful",
            max_tokens=1500,
            temperature=0.0,
        )
        plan = self._parse(plan_raw)
        if not plan:
            return PlanResult(final_text="Couldn't draft a plan.")

        steps = [
            PlanStep(skill=s.get("skill", ""), input=s.get("input", ""), why=s.get("why", ""))
            for s in plan.get("steps", [])
        ][:max_steps]

        outputs: list[str] = []
        for step in steps:
            skill = self.registry.get(step.skill)
            if skill is None:
                step.output = f"(skill {step.skill} not registered)"
                outputs.append(step.output)
                continue
            ctx = await self.builder.build(user_id, step.input)
            try:
                response = await skill.execute(step.input, ctx)
                step.output = response.text or ""
            except Exception as exc:
                logger.warning("Plan step %s failed: %s", step.skill, exc)
                step.output = f"(error: {exc})"
            outputs.append(step.output)

        final_text = self._compose(goal, steps, plan.get("final", ""))
        return PlanResult(final_text=final_text, steps=steps)

    @staticmethod
    def _parse(raw: str) -> dict[str, Any] | None:
        s = raw.strip()
        m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
        if m:
            s = m.group(1)
        try:
            return json.loads(s)
        except Exception as exc:
            logger.warning("Plan JSON parse failed: %s", exc)
            return None

    @staticmethod
    def _compose(goal: str, steps: list[PlanStep], summary: str) -> str:
        parts = [f"🎯 Goal: {goal}"]
        for i, step in enumerate(steps, 1):
            parts.append(f"\n**Step {i} — {step.skill}** ({step.why})")
            parts.append(step.output[:1500] + ("…" if len(step.output) > 1500 else ""))
        if summary:
            parts.append(f"\n_{summary}_")
        return "\n".join(parts)
