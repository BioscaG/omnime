"""Encryption helpers no-op without key, encrypt/decrypt roundtrip with key."""
from __future__ import annotations

import importlib

import pytest


def test_noop_when_key_absent(monkeypatch):
    from src import config as config_module
    from src.utils import crypto

    monkeypatch.setattr(config_module.settings, "encryption_key", "")
    crypto._fernet.cache_clear()  # type: ignore[attr-defined]

    assert crypto.encrypt("secret") == "secret"
    assert crypto.decrypt("secret") == "secret"
    assert not crypto.is_enabled()


def test_roundtrip_with_key(monkeypatch):
    from cryptography.fernet import Fernet

    from src import config as config_module
    from src.utils import crypto

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(config_module.settings, "encryption_key", key)
    crypto._fernet.cache_clear()  # type: ignore[attr-defined]

    enc = crypto.encrypt("super secret")
    assert enc != "super secret"
    assert crypto.decrypt(enc) == "super secret"
    assert crypto.is_enabled()
