"""Browser agent JSON parsing + credential vault round-trip."""
from __future__ import annotations

from cryptography.fernet import Fernet

import pytest

from src.skills.browser_agent import BrowserAgentSkill


def test_parse_valid_json():
    raw = """```json
    {"action": "click", "selector": "Login", "reasoning": "Need to log in"}
    ```"""
    parsed = BrowserAgentSkill._parse_json(raw)
    assert parsed["action"] == "click"
    assert parsed["selector"] == "Login"


def test_parse_invalid_json_returns_none():
    assert BrowserAgentSkill._parse_json("not json") is None


def test_format_step_has_action_and_reasoning():
    from src.skills.browser_agent import AgentStep

    step = AgentStep(action="click", selector="Submit", reasoning="continue")
    formatted = BrowserAgentSkill._format_step(3, step)
    assert "Step 3" in formatted
    assert "click" in formatted
    assert "Submit" in formatted


def test_credential_vault_roundtrip(monkeypatch, memory_manager):
    from src import config as config_module
    from src.utils import crypto
    from src.utils.credentials import CredentialVault

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(config_module.settings, "encryption_key", key)
    crypto._fernet.cache_clear()  # type: ignore[attr-defined]

    user_id = memory_manager.ensure_user(telegram_id=1)
    CredentialVault.store(
        user_id=user_id, site="renfe.com",
        username="myuser", password="hunter2", login_url="https://renfe.com/login",
    )
    creds = CredentialVault.get(user_id, "renfe.com")
    assert creds is not None
    assert creds.username == "myuser"
    assert creds.password == "hunter2"
    assert "renfe.com" in CredentialVault.list_sites(user_id)


def test_credential_vault_refuses_without_key(monkeypatch, memory_manager):
    from src import config as config_module
    from src.utils import crypto
    from src.utils.credentials import CredentialVault

    monkeypatch.setattr(config_module.settings, "encryption_key", "")
    crypto._fernet.cache_clear()  # type: ignore[attr-defined]

    user_id = memory_manager.ensure_user(telegram_id=2)
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        CredentialVault.store(user_id=user_id, site="x", username="u", password="p")
