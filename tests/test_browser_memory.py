"""Browser learning layer: recipes, site notes, prompt priming."""
from __future__ import annotations

import pytest

from src.skills.browser_memory import BrowserMemory, domain_of, render_priming


def test_domain_of_strips_www_and_scheme():
    assert domain_of("https://www.renfe.com/es/es") == "renfe.com"
    assert domain_of("http://Example.COM/foo") == "example.com"
    assert domain_of("about:blank") == ""
    assert domain_of("") == ""


@pytest.mark.asyncio
async def test_save_and_retrieve_recipe(memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=1)
    BrowserMemory.save_recipe(
        user_id=user_id,
        domain="renfe.com",
        goal_template="busca tren Barcelona-Zaragoza",
        steps=[
            {"action": "goto", "url": "https://www.renfe.com"},
            {"action": "type", "selector_type": "label", "selector": "Origen", "value": "Barcelona"},
        ],
    )
    recipes = BrowserMemory.find_recipes(user_id, "renfe.com")
    assert len(recipes) == 1
    assert recipes[0].steps[0]["action"] == "goto"
    assert recipes[0].uses == 1


@pytest.mark.asyncio
async def test_save_recipe_increments_on_repeat(memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=2)
    for _ in range(3):
        BrowserMemory.save_recipe(
            user_id=user_id,
            domain="renfe.com",
            goal_template="x",
            steps=[{"action": "goto", "url": "x"}],
        )
    recipes = BrowserMemory.find_recipes(user_id, "renfe.com")
    assert len(recipes) == 1
    assert recipes[0].uses == 3
    assert recipes[0].successes == 3


@pytest.mark.asyncio
async def test_notes_dedup_via_unique_constraint(memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=3)
    BrowserMemory.add_note(user_id, "renfe.com", "Origen needs type, not fill")
    BrowserMemory.add_note(user_id, "renfe.com", "Origen needs type, not fill")
    notes = BrowserMemory.get_notes(user_id, "renfe.com")
    assert len(notes) == 1


def test_render_priming_returns_empty_when_nothing():
    assert render_priming([], []) == ""


def test_render_priming_includes_recipes_and_notes():
    from src.skills.browser_memory import Recipe

    recipe = Recipe(
        id=1, domain="renfe.com", goal_template="train search",
        steps=[{"action": "goto", "url": "x"}],
        uses=2, successes=2,
    )
    out = render_priming([recipe], ["Origen needs type, not fill"])
    assert "Recipe" in out and "renfe.com" not in out  # we don't repeat domain in body
    assert "Origen" in out
