from pathlib import Path

from eeg_task_scheduler.db import Database
from eeg_task_scheduler.gemini_client import GeminiClient
from eeg_task_scheduler.rag import RagStore


def test_rag_sqlite_fallback_roundtrip(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite3")
    gemini = GeminiClient(None, "test-model", "test-embedding")
    rag = RagStore(tmp_path, database, gemini)
    rag.collection = None
    chunk_id = rag.add("高負荷で停滞したのでTodoを小さくした", {"type": "report"}, "test:1", "s1")
    assert chunk_id
    assert rag.query("高負荷", limit=1)

