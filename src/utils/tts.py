"""Text-to-speech helper. Optional — disabled when no provider is configured."""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from src.config import settings


logger = logging.getLogger(__name__)


async def synthesize(text: str, voice: str = "alloy") -> Path | None:
    """Render ``text`` to an OGG file using OpenAI's TTS API.

    Returns the file path or ``None`` if no API key is configured.
    """
    if not settings.openai_api_key:
        return None
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    out = settings.uploads_dir / f"tts_{abs(hash(text)) & 0xFFFFFFFF:x}.ogg"
    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            r = await http.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={
                    "model": "gpt-4o-mini-tts",
                    "voice": voice,
                    "input": text[:4000],
                    "format": "opus",
                },
            )
            r.raise_for_status()
            out.write_bytes(r.content)
            return out
    except Exception as exc:
        logger.warning("TTS synthesis failed: %s", exc)
        return None
