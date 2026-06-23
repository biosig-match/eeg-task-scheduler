from pathlib import Path

from eeg_task_scheduler.classifier import ActivityWindow
from eeg_task_scheduler.db import Database
from eeg_task_scheduler.gemini_client import GeminiClient
from eeg_task_scheduler.rag import RagStore
from eeg_task_scheduler.session import CURRENT_ACTIVITY_LIMIT, CURRENT_EEG_LIMIT, CURRENT_EVENT_LIMIT, SessionService


def test_episode_roundtrip(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite3")
    gemini = GeminiClient(None, "test-model", "test-embedding")
    rag = RagStore(tmp_path, database, gemini)
    rag.collection = None
    service = SessionService(database, rag, gemini, tmp_path / "captures")
    first_session_id = service.start("考察を書く", {"mock": True})
    service.add_eeg_window(
        "2026-06-23T00:00:00+00:00",
        "2026-06-23T00:00:30+00:00",
        {"theta": 1.0, "alpha": 1.0, "beta": 1.0, "engagement": 0.5, "workload": 1.3},
        [],
    )
    result = service.add_episode(
        "2026-06-23T00:00:00+00:00",
        "2026-06-23T00:00:30+00:00",
        "Code - app.py",
        ActivityWindow(0, 0.0, 0, 0, 60.0),
        screen_description="エラー箇所を見ている",
    )
    assert result["episode_id"]
    current = service.current()
    assert len(current["episodes"]) == 1
    assert len(current["activity_windows"]) == 1
    assert current["activity_windows"][0]["idle_seconds"] == 60.0
    assert current["episodes"][0]["label"] == "過負荷停止"
    report = service.stop()
    assert report["summary"]
    assert report["todo_suggestions"]
    assert "phases" in report
    service.start("次の考察を書く", {"mock": True})
    next_current = service.current()
    baseline = next_current["normalization_baseline"]
    assert baseline["source_session_id"] == first_session_id
    assert baseline["eeg"]["workload"]["mean"] == 1.3
    assert baseline["activity"]["key_count"]["mean"] == 0.0


def test_current_limits_large_live_payload(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.sqlite3")
    gemini = GeminiClient(None, "test-model", "test-embedding")
    rag = RagStore(tmp_path, database, gemini)
    rag.collection = None
    service = SessionService(database, rag, gemini, tmp_path / "captures")
    service.start("live task", {"mock": True})

    total_windows = CURRENT_EVENT_LIMIT + 25
    for index in range(total_windows):
        service.add_activity(
            f"2026-06-23T00:{index // 60:02d}:{index % 60:02d}+00:00",
            f"2026-06-23T00:{index // 60:02d}:{index % 60:02d}+00:00",
            ActivityWindow(index, float(index * 10), index % 8, index % 3, 0.0),
        )
        service.add_eeg_window(
            f"2026-06-23T00:{index // 60:02d}:{index % 60:02d}+00:00",
            f"2026-06-23T00:{index // 60:02d}:{index % 60:02d}+00:00",
            {"theta": 1.0, "alpha": 1.0, "beta": 1.0, "engagement": 0.5, "workload": 1.0},
            [],
        )

    current = service.current()

    assert len(current["eeg_windows"]) == CURRENT_EEG_LIMIT
    assert len(current["events"]) == CURRENT_EVENT_LIMIT
    assert len(current["activity_windows"]) == CURRENT_ACTIVITY_LIMIT
    assert current["eeg_windows"][0]["started_at"] < current["eeg_windows"][-1]["started_at"]
    assert current["events"][0]["started_at"] < current["events"][-1]["started_at"]
    assert current["activity_windows"][0]["started_at"] < current["activity_windows"][-1]["started_at"]
