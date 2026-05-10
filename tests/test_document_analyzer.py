"""Document analyzer: classification, summary, chunking."""
from __future__ import annotations

import json

import pytest

from src.skills.document_analyzer import DocumentAnalyzer, chunk_text


@pytest.mark.asyncio
async def test_analyze_parses_classification(fake_llm):
    fake_llm.responses["document classifier"] = json.dumps({
        "category": "cv",
        "title": "Guido Biosca CV",
        "summary": "ML engineer CV with experience at Multiverse Computing.",
        "tags": ["cv", "ml", "engineer"],
        "language": "en",
        "key_entities": ["Multiverse Computing", "Guido Biosca"],
        "should_extract_personal": True,
    })
    analyzer = DocumentAnalyzer(fake_llm)
    result = await analyzer.analyze("Guido Biosca\nMachine Learning Engineer\n...")
    assert result.category == "cv"
    assert result.should_extract_personal is True
    assert "Multiverse Computing" in result.key_entities


@pytest.mark.asyncio
async def test_analyze_returns_default_on_invalid_json(fake_llm):
    fake_llm.responses["document classifier"] = "not json"
    analyzer = DocumentAnalyzer(fake_llm)
    result = await analyzer.analyze("Some random text")
    assert result.category == "other"
    assert result.summary == ""


@pytest.mark.asyncio
async def test_analyze_skips_empty_text(fake_llm):
    analyzer = DocumentAnalyzer(fake_llm)
    result = await analyzer.analyze("")
    assert result.category == "other"
    assert fake_llm.calls == []  # no LLM call for empty input


def test_chunk_short_text_is_single_chunk():
    chunks = chunk_text("short text", target_chars=1800)
    assert chunks == ["short text"]


def test_chunk_paragraphs_split_on_blank_lines():
    paragraphs = "\n\n".join(["paragraph " + str(i) * 200 for i in range(5)])
    chunks = chunk_text(paragraphs, target_chars=1000, overlap=100)
    assert len(chunks) >= 2
    assert all(len(c) <= 1100 for c in chunks)


def test_chunk_oversized_paragraph_splits_with_overlap():
    long_para = "a" * 5000
    chunks = chunk_text(long_para, target_chars=1000, overlap=100)
    assert len(chunks) >= 5
    # Each chunk should be near target size.
    for c in chunks:
        assert len(c) <= 1000
