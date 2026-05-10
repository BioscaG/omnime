"""Dynamic registry that selects the best skill for a given message."""
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.skills.base import BaseSkill

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


class SkillRegistry:
    def __init__(
        self,
        llm: "LLMClient",
        memory: "MemoryManager",
    ) -> None:
        self.llm = llm
        self.memory = memory
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        self._skills[skill.name] = skill
        logger.info("Skill registered: %s", skill.name)

    def unregister(self, name: str) -> None:
        self._skills.pop(name, None)

    def list_skills(self) -> list[BaseSkill]:
        return list(self._skills.values())

    def get(self, name: str) -> Optional[BaseSkill]:
        return self._skills.get(name)

    def find_best_skill(self, message: str, context: "Context") -> Optional[BaseSkill]:
        scores: list[tuple[float, BaseSkill]] = []
        for skill in self._skills.values():
            try:
                score = skill.can_handle(message, intent=context.intent)
            except Exception as exc:
                logger.warning("Skill %s scoring failed: %s", skill.name, exc)
                score = 0.0
            scores.append((score, skill))
        scores.sort(key=lambda s: s[0], reverse=True)
        if scores and scores[0][0] >= 0.4:
            return scores[0][1]
        return None

    def reload(self, name: str) -> bool:
        """Hot-reload a skill module by name. Returns True on success."""
        skill = self._skills.get(name)
        module_name = f"src.skills.{name}"
        try:
            module = importlib.import_module(module_name)
            module = importlib.reload(module)
            cls = getattr(module, skill.__class__.__name__) if skill else None
            if cls is None:
                return False
            new_skill = cls(self.llm, self.memory)
            self._skills[name] = new_skill
            return True
        except Exception as exc:
            logger.error("Hot reload failed for %s: %s", name, exc)
            return False


_registry: Optional[SkillRegistry] = None


def get_registry(
    llm: Optional["LLMClient"] = None,
    memory: Optional["MemoryManager"] = None,
) -> SkillRegistry:
    global _registry
    if _registry is None:
        if llm is None or memory is None:
            raise RuntimeError("First call to get_registry needs llm + memory")
        _registry = SkillRegistry(llm, memory)
        _register_default_skills(_registry)
    return _registry


def _register_default_skills(registry: SkillRegistry) -> None:
    from src.skills.cv_generator import CVGeneratorSkill
    from src.skills.email_composer import EmailComposerSkill
    from src.skills.document_generator import DocumentGeneratorSkill
    from src.skills.web_researcher import WebResearcherSkill
    from src.skills.daily_briefing import DailyBriefingSkill
    from src.skills.code_generator import CodeGeneratorSkill
    from src.skills.weekly_review import WeeklyReviewSkill
    from src.skills.career_onboarding import CareerOnboardingSkill
    from src.skills.interview_prep import InterviewPrepSkill
    from src.skills.job_tracker import JobTrackerSkill
    from src.skills.cv_variants import CVVariantsSkill, SkillGapSkill
    from src.skills.knowledge_graph import KnowledgeGraphSkill
    from src.skills.reading_list import ReadingListSkill
    from src.skills.decision_log import DecisionLogSkill
    from src.skills.journaling import JournalingSkill
    from src.skills.time_machine import TimeMachineSkill
    from src.skills.agentic import AgenticSkill
    from src.skills.web_fetch import WebFetchSkill
    from src.skills.browser_agent import BrowserAgentSkill

    for cls in (
        CVGeneratorSkill,
        EmailComposerSkill,
        DocumentGeneratorSkill,
        WebResearcherSkill,
        DailyBriefingSkill,
        CodeGeneratorSkill,
        WeeklyReviewSkill,
        CareerOnboardingSkill,
        InterviewPrepSkill,
        JobTrackerSkill,
        CVVariantsSkill,
        SkillGapSkill,
        KnowledgeGraphSkill,
        ReadingListSkill,
        DecisionLogSkill,
        JournalingSkill,
        TimeMachineSkill,
        AgenticSkill,
        WebFetchSkill,
        BrowserAgentSkill,
    ):
        try:
            registry.register(cls(registry.llm, registry.memory))
        except Exception as exc:
            logger.warning("Skipping skill %s: %s", cls.__name__, exc)
