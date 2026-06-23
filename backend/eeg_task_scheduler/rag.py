from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .db import Database, to_json
from .gemini_client import GeminiClient
from .time_utils import now_iso


class RagStore:
    def __init__(self, data_dir: Path, database: Database, gemini: GeminiClient) -> None:
        self.database = database
        self.gemini = gemini
        self.collection: Any | None = None
        try:
            import chromadb

            client = chromadb.PersistentClient(path=str(data_dir / "chroma"))
            self.collection = client.get_or_create_collection("pomodoro_memory")
        except Exception:
            self.collection = None

    def add(self, text: str, metadata: dict[str, Any], source_ref: str, session_id: str | None = None) -> str:
        chunk_id = hashlib.sha256(f"{source_ref}\n{text}".encode("utf-8")).hexdigest()[:24]
        now = now_iso()
        self.database.execute(
            """
            INSERT OR REPLACE INTO memory_chunks
            (id, session_id, created_at, text, metadata_json, source_ref)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chunk_id, session_id, now, text, to_json(metadata), source_ref),
        )
        if self.collection is not None:
            embedding = self.gemini.embed(text)
            kwargs: dict[str, Any] = {
                "ids": [chunk_id],
                "documents": [text],
                "metadatas": [{**metadata, "source_ref": source_ref, "session_id": session_id or ""}],
            }
            if embedding is not None:
                kwargs["embeddings"] = [embedding]
            try:
                self.collection.upsert(**kwargs)
            except Exception:
                pass
        return chunk_id

    def query(self, text: str, limit: int = 5) -> list[str]:
        if self.collection is not None:
            try:
                embedding = self.gemini.embed(text)
                if embedding is not None:
                    result = self.collection.query(query_embeddings=[embedding], n_results=limit)
                else:
                    result = self.collection.query(query_texts=[text], n_results=limit)
                documents = result.get("documents") or [[]]
                return [str(item) for item in documents[0]]
            except Exception:
                pass
        rows = self.database.query(
            "SELECT text FROM memory_chunks WHERE text LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{text[:20]}%", limit),
        )
        if rows:
            return [str(row["text"]) for row in rows]
        rows = self.database.query("SELECT text FROM memory_chunks ORDER BY created_at DESC LIMIT ?", (limit,))
        return [str(row["text"]) for row in rows]
