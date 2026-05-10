"""Process-wide registry for the running Telegram Application.

Tools (which run in async tasks spawned by the orchestrator) sometimes need
to call into ``application.bot.send_document(...)`` etc. We don't want to
thread the Application object through every tool's signature, so we stash
it here at boot and tools fetch it via ``get_application()``.
"""
from __future__ import annotations

from typing import Optional


_APPLICATION = None


def set_application(app) -> None:
    global _APPLICATION
    _APPLICATION = app


def get_application():
    return _APPLICATION
