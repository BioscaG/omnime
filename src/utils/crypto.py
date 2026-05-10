"""Symmetric encryption helpers for sensitive fields at rest."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from src.config import settings


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _fernet():
    if not settings.encryption_key:
        return None
    try:
        from cryptography.fernet import Fernet

        return Fernet(settings.encryption_key.encode())
    except Exception as exc:
        logger.warning("Could not initialise Fernet (encryption disabled): %s", exc)
        return None


def encrypt(value: Optional[str]) -> Optional[str]:
    """Encrypt with the configured key. No-op when key absent."""
    if value is None:
        return None
    f = _fernet()
    if f is None:
        return value
    return f.encrypt(value.encode()).decode()


def decrypt(value: Optional[str]) -> Optional[str]:
    """Decrypt; if value isn't ciphertext returns it unchanged."""
    if value is None:
        return None
    f = _fernet()
    if f is None:
        return value
    try:
        return f.decrypt(value.encode()).decode()
    except Exception:
        return value


def is_enabled() -> bool:
    return _fernet() is not None
