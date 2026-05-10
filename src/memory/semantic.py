"""Semantic (vector) memory backed by ChromaDB."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from src.config import settings


logger = logging.getLogger(__name__)


@dataclass
class SemanticHit:
    id: str
    text: str
    metadata: dict[str, Any]
    distance: float


class SemanticStore:
    """Thin wrapper around ChromaDB collections.

    Falls back to a no-op in-memory store if ChromaDB is unreachable —
    keeps the bot operational with reduced capabilities.
    """

    DEFAULT_COLLECTIONS = ("conversations", "knowledge", "documents")

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        in_memory: bool = False,
    ) -> None:
        self._client = None
        self._collections: dict[str, Any] = {}
        self._fallback: dict[str, list[dict[str, Any]]] = {
            c: [] for c in self.DEFAULT_COLLECTIONS
        }
        self._available = False

        try:
            import chromadb

            if in_memory:
                self._client = chromadb.EphemeralClient()
            else:
                host = host or settings.chroma_host
                port = port or settings.chroma_port
                self._client = chromadb.HttpClient(host=host, port=port)
            for name in self.DEFAULT_COLLECTIONS:
                self._collections[name] = self._client.get_or_create_collection(name)
            self._available = True
        except Exception as exc:
            logger.warning("ChromaDB unavailable, semantic search disabled: %s", exc)

    @property
    def available(self) -> bool:
        return self._available

    def add(
        self,
        collection: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        doc_id: str | None = None,
    ) -> str:
        doc_id = doc_id or str(uuid.uuid4())
        metadata = metadata or {}
        if not self._available:
            self._fallback.setdefault(collection, []).append(
                {"id": doc_id, "text": text, "metadata": metadata}
            )
            return doc_id
        col = self._collections.get(collection) or self._client.get_or_create_collection(collection)
        col.add(documents=[text], metadatas=[metadata], ids=[doc_id])
        return doc_id

    def search(
        self,
        collection: str,
        query: str,
        n_results: int = 5,
        where: Optional[dict[str, Any]] = None,
    ) -> list[SemanticHit]:
        if not self._available:
            entries = self._fallback.get(collection, [])
            q_low = query.lower()
            scored = [
                SemanticHit(
                    id=e["id"],
                    text=e["text"],
                    metadata=e["metadata"],
                    distance=0.0 if q_low in e["text"].lower() else 1.0,
                )
                for e in entries
            ]
            scored.sort(key=lambda h: h.distance)
            return scored[:n_results]

        col = self._collections.get(collection)
        if col is None:
            return []
        try:
            res = col.query(query_texts=[query], n_results=n_results, where=where)
        except Exception as exc:
            logger.warning("Semantic search failed: %s", exc)
            return []

        hits: list[SemanticHit] = []
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, doc_id in enumerate(ids):
            hits.append(
                SemanticHit(
                    id=doc_id,
                    text=docs[i] if i < len(docs) else "",
                    metadata=metas[i] if i < len(metas) else {},
                    distance=dists[i] if i < len(dists) else 0.0,
                )
            )
        return hits

    def delete(self, collection: str, doc_id: str) -> None:
        if not self._available:
            entries = self._fallback.get(collection, [])
            self._fallback[collection] = [e for e in entries if e["id"] != doc_id]
            return
        col = self._collections.get(collection)
        if col is not None:
            col.delete(ids=[doc_id])

    def delete_where(self, collection: str, where: dict) -> None:
        """Delete every entry matching the where filter (e.g. all chunks
        of a single file). Used by file/entity deletion to clean up the
        vector index alongside the structured row."""
        if not self._available:
            entries = self._fallback.get(collection, [])
            self._fallback[collection] = [
                e for e in entries
                if not all(
                    (e.get("metadata") or {}).get(k) == v
                    for k, v in where.items()
                )
            ]
            return
        col = self._collections.get(collection)
        if col is not None:
            try:
                col.delete(where=where)
            except Exception as exc:
                logger.warning("delete_where(%s, %s) failed: %s", collection, where, exc)

    def count(self, collection: str) -> int:
        if not self._available:
            return len(self._fallback.get(collection, []))
        col = self._collections.get(collection)
        if col is None:
            return 0
        try:
            return col.count()
        except Exception:
            return 0
