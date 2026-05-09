"""Skills — pluggable capabilities the orchestrator can dispatch to."""

from src.skills.base import BaseSkill, SkillResponse
from src.skills.registry import SkillRegistry, get_registry

__all__ = ["BaseSkill", "SkillResponse", "SkillRegistry", "get_registry"]
