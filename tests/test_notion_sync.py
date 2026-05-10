"""Notion property serialisation + extraction helpers."""
from __future__ import annotations

import pytest

from src.integrations import notion_client as nc
from src.integrations.notion_sync import (
    _extract_multi,
    _extract_rich,
    _extract_select,
    _extract_title,
)


def test_title_property_shape():
    out = nc.title("Hello")
    assert out["title"][0]["text"]["content"] == "Hello"


def test_multi_select_filters_empty():
    out = nc.multi_select(["a", "", None, "b"])
    assert [v["name"] for v in out["multi_select"]] == ["a", "b"]


def test_select_handles_none():
    assert nc.select(None) == {"select": None}


def test_date_property_handles_none():
    assert nc.date_property(None) == {"date": None}


def test_extractors_round_trip():
    assert _extract_title({"title": [{"plain_text": "Hi"}]}) == "Hi"
    assert _extract_rich({"rich_text": [{"plain_text": "Body"}]}) == "Body"
    assert _extract_select({"select": {"name": "active"}}) == "active"
    assert _extract_multi({"multi_select": [{"name": "a"}, {"name": "b"}]}) == ["a", "b"]
