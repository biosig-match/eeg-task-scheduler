from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    todo TEXT NOT NULL,
    status TEXT NOT NULL,
    settings_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eeg_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    theta REAL NOT NULL,
    alpha REAL NOT NULL,
    beta REAL NOT NULL,
    engagement REAL NOT NULL,
    workload REAL NOT NULL,
    approach_avoidance REAL NOT NULL DEFAULT 0,
    approach_avoidance_available INTEGER NOT NULL DEFAULT 0,
    electrode_names_json TEXT NOT NULL DEFAULT '[]',
    signal_quality_json TEXT NOT NULL,
    excluded_reason TEXT
);
CREATE TABLE IF NOT EXISTS screen_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    source_name TEXT NOT NULL,
    image_path TEXT,
    description TEXT NOT NULL,
    ocr_text TEXT NOT NULL,
    privacy_state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS input_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    key_count INTEGER NOT NULL,
    mouse_distance REAL NOT NULL,
    click_count INTEGER NOT NULL,
    scroll_count INTEGER NOT NULL,
    idle_seconds REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    label TEXT NOT NULL,
    severity TEXT NOT NULL,
    reason TEXT NOT NULL,
    related_observation_id INTEGER
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    todo TEXT NOT NULL,
    active_window TEXT NOT NULL,
    observation_id INTEGER,
    eeg_window_id INTEGER,
    activity_id INTEGER,
    label TEXT NOT NULL,
    severity TEXT NOT NULL,
    work_summary TEXT NOT NULL,
    embedding_ref TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_phases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    phase_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    episode_ids_json TEXT NOT NULL,
    completed INTEGER NOT NULL,
    evidence TEXT NOT NULL,
    next_task TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    summary TEXT NOT NULL,
    todo_suggestions_json TEXT NOT NULL,
    approval_state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_chunks (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    created_at TEXT NOT NULL,
    text TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    source_ref TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_eeg_windows_session_started ON eeg_windows(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_screen_observations_session_captured ON screen_observations(session_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_events_session_started ON events(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_episodes_session_started ON episodes(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_task_phases_session_started ON task_phases(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_reports_session_created ON reports(session_id, created_at);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(task_phases)").fetchall()
            }
            if "next_task" not in columns:
                connection.execute("ALTER TABLE task_phases ADD COLUMN next_task TEXT NOT NULL DEFAULT ''")
            eeg_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(eeg_windows)").fetchall()
            }
            if "approach_avoidance" not in eeg_columns:
                connection.execute("ALTER TABLE eeg_windows ADD COLUMN approach_avoidance REAL NOT NULL DEFAULT 0")
            if "approach_avoidance_available" not in eeg_columns:
                connection.execute(
                    "ALTER TABLE eeg_windows ADD COLUMN approach_avoidance_available INTEGER NOT NULL DEFAULT 0"
                )
            if "electrode_names_json" not in eeg_columns:
                connection.execute("ALTER TABLE eeg_windows ADD COLUMN electrode_names_json TEXT NOT NULL DEFAULT '[]'")

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.connect() as connection:
            cursor = connection.execute(sql, params)
            return int(cursor.lastrowid)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def from_json(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
