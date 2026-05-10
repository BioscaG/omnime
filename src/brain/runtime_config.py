"""Tiny mutable config layer that survives restarts.

Why not just env vars? Env vars require editing .env + ``docker compose
up -d`` to reload — too heavy for "switch the driver model real quick".
This is a JSON file at ``data/runtime_config.json`` with a get/set API
that any code path can hit. Reads are cheap (file is tiny + cached);
writes flush to disk immediately so the value survives a restart.

Defaults fall through to env-var settings, so users that don't touch
``/model`` get the .env behaviour they expect.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from src.config import settings


logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None


def _path() -> Path:
    base = Path(getattr(settings, "data_dir", "/app/data"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "runtime_config.json"


def _load() -> dict[str, Any]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    p = _path()
    if not p.exists():
        _CACHE = {}
        return _CACHE
    try:
        _CACHE = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(_CACHE, dict):
            _CACHE = {}
    except Exception as exc:
        logger.warning("runtime_config load failed: %s", exc)
        _CACHE = {}
    return _CACHE


def get(key: str, default: Any = None) -> Any:
    with _LOCK:
        return _load().get(key, default)


def set_value(key: str, value: Any) -> None:
    with _LOCK:
        cfg = _load()
        cfg[key] = value
        try:
            _path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("runtime_config save failed: %s", exc)


def all_values() -> dict[str, Any]:
    with _LOCK:
        return dict(_load())


# --- Convenience accessors -----------------------------------------------

VALID_TIERS = ("tiny", "fast", "powerful")
AUTO = "auto"
ALL_VALUES = VALID_TIERS + (AUTO,)
TIER_LABELS = {
    AUTO: "automatic — heuristic picks per message",
    "tiny": "Haiku 4.5 — fast & cheap",
    "fast": "Sonnet 4.6 — balanced",
    "powerful": "Opus 4.7 — strongest reasoning, ~5× cost",
}


def agentic_model_tier() -> str:
    """Returns the user's CHOICE — could be 'auto' or a specific tier.
    Use ``is_auto_mode()`` to distinguish; use ``effective_tier(message)``
    to get the actual tier to use for a given message."""
    val = get("agentic_model_tier")
    if val in ALL_VALUES:
        return val
    return AUTO  # default to auto


def is_auto_mode() -> bool:
    return agentic_model_tier() == AUTO


def set_agentic_model_tier(value: str) -> None:
    if value not in ALL_VALUES:
        raise ValueError(f"invalid tier: {value}. Must be one of {ALL_VALUES}")
    set_value("agentic_model_tier", value)
