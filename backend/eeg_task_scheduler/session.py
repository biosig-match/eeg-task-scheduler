from __future__ import annotations

from pathlib import Path
import uuid

from .classifier import ActivityWindow, classify_state
from .db import Database, from_json, to_json
from .gemini_client import GeminiClient
from .rag import RagStore
from .time_utils import now_iso, now_tokyo

CURRENT_EEG_LIMIT = 240
CURRENT_EVENT_LIMIT = 400
CURRENT_ACTIVITY_LIMIT = 240
CURRENT_EPISODE_LIMIT = 80
CURRENT_REPORT_LIMIT = 5

EEG_NORMALIZATION_COLUMNS = ("engagement", "workload", "approach_avoidance")
ACTIVITY_NORMALIZATION_COLUMNS = ("key_count", "mouse_distance", "click_count", "scroll_count")


class SessionService:
    def __init__(self, database: Database, rag: RagStore, gemini: GeminiClient, capture_dir: Path) -> None:
        self.database = database
        self.rag = rag
        self.gemini = gemini
        self.capture_dir = capture_dir
        self.active_session_id: str | None = None
        self._last_activity = ActivityWindow(0, 0.0, 0, 0, 0.0)
        self._last_eeg_window_id: int | None = None
        self._last_eeg_features: dict[str, object] = {}
        self._last_observation_id: int | None = None
        self._last_observation_description = ""

    def start(self, todo: str, settings: dict[str, object]) -> str:
        if self.active_session_id:
            return self.active_session_id
        session_id = now_tokyo().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.database.execute(
            "INSERT INTO sessions (id, started_at, todo, status, settings_json) VALUES (?, ?, ?, ?, ?)",
            (session_id, now_iso(), todo, "active", to_json(settings)),
        )
        self.active_session_id = session_id
        self.rag.add(f"開始Todo: {todo}", {"type": "todo"}, f"session:{session_id}:todo", session_id)
        return session_id

    def stop(self) -> dict[str, object]:
        session_id = self.require_active()
        self.database.execute(
            "UPDATE sessions SET ended_at = ?, status = ? WHERE id = ?",
            (now_iso(), "stopped", session_id),
        )
        self.active_session_id = None
        phases = self.build_phases(session_id)
        timeline = self.database.query(
            "SELECT label, severity, reason, started_at, ended_at FROM events WHERE session_id = ? ORDER BY started_at",
            (session_id,),
        )
        session = self.database.query_one("SELECT todo FROM sessions WHERE id = ?", (session_id,))
        todo = str(session["todo"] if session else "")
        memories = self.rag.query(todo, limit=4)
        summary, suggestions = self.gemini.summarize_report(todo, timeline, memories)
        next_tasks = [str(phase.get("next_task", "")).strip() for phase in phases if str(phase.get("next_task", "")).strip()]
        if next_tasks:
            suggestions = next_tasks[:3]
        report_id = self.database.execute(
            """
            INSERT INTO reports (session_id, created_at, summary, todo_suggestions_json, approval_state)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, now_iso(), summary, to_json(suggestions), "pending"),
        )
        self.rag.add(summary, {"type": "report", "report_id": report_id}, f"session:{session_id}:report", session_id)
        return {"session_id": session_id, "summary": summary, "todo_suggestions": suggestions, "phases": phases}

    def abort(self) -> dict[str, object]:
        session_id = self.require_active()
        self.database.execute(
            "UPDATE sessions SET ended_at = ?, status = ? WHERE id = ?",
            (now_iso(), "stopped", session_id),
        )
        self.active_session_id = None
        return {"session_id": session_id, "summary": "セッションを停止しました。", "todo_suggestions": [], "phases": []}

    def build_phases(self, session_id: str) -> list[dict[str, object]]:
        existing = self.database.query("SELECT * FROM task_phases WHERE session_id = ? ORDER BY started_at", (session_id,))
        if existing:
            for phase in existing:
                phase["episode_ids"] = from_json(str(phase.pop("episode_ids_json")), [])
                phase["completed"] = bool(phase["completed"])
            return existing
        session = self.database.query_one("SELECT todo FROM sessions WHERE id = ?", (session_id,))
        task = str(session["todo"] if session else "")
        episodes = self.database.query("SELECT * FROM episodes WHERE session_id = ? ORDER BY started_at", (session_id,))
        phases = self.gemini.summarize_phases(task, episodes)
        stored = []
        for phase in phases:
            episode_ids = phase.get("episode_ids") or phase.get("episode_ids_json") or []
            started_at = str(phase.get("started_at") or (episodes[0]["started_at"] if episodes else now_iso()))
            ended_at = str(phase.get("ended_at") or (episodes[-1]["ended_at"] if episodes else started_at))
            phase_id = self.database.execute(
                """
                INSERT INTO task_phases
                (session_id, started_at, ended_at, phase_type, title, summary, episode_ids_json, completed, evidence, next_task)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    started_at,
                    ended_at,
                    str(phase.get("phase_type", "作業")),
                    str(phase.get("title", "作業フェーズ")),
                    str(phase.get("summary", "")),
                    to_json(episode_ids),
                    1 if bool(phase.get("completed")) else 0,
                    str(phase.get("evidence", "")),
                    str(phase.get("next_task", "")),
                ),
            )
            phase["id"] = phase_id
            phase["episode_ids"] = episode_ids
            phase["started_at"] = started_at
            phase["ended_at"] = ended_at
            stored.append(phase)
        return stored

    def add_activity(self, started_at: str, ended_at: str, activity: ActivityWindow) -> int:
        session_id = self.require_active()
        self._last_activity = activity
        return self.database.execute(
            """
            INSERT INTO input_activity
            (session_id, started_at, ended_at, key_count, mouse_distance, click_count, scroll_count, idle_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                started_at,
                ended_at,
                activity.key_count,
                activity.mouse_distance,
                activity.click_count,
                activity.scroll_count,
                activity.idle_seconds,
            ),
        )

    def add_eeg_window(self, started_at: str, ended_at: str, features: dict[str, object], quality: list[dict[str, object]]) -> int:
        session_id = self.require_active()
        theta = float(features.get("theta", 0.0))
        alpha = float(features.get("alpha", 0.0))
        beta = float(features.get("beta", 0.0))
        engagement = float(features.get("engagement", 0.0))
        workload = float(features.get("workload", 0.0))
        approach_avoidance = float(features.get("approach_avoidance", 0.0))
        approach_avoidance_available = bool(features.get("approach_avoidance_available", False))
        electrode_names = features.get("electrode_names", [])
        window_id = self.database.execute(
            """
            INSERT INTO eeg_windows
            (session_id, started_at, ended_at, theta, alpha, beta, engagement, workload,
             approach_avoidance, approach_avoidance_available, electrode_names_json, signal_quality_json,
             excluded_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                started_at,
                ended_at,
                theta,
                alpha,
                beta,
                engagement,
                workload,
                approach_avoidance,
                int(approach_avoidance_available),
                to_json(electrode_names),
                to_json(quality),
                None,
            ),
        )
        self._last_eeg_window_id = window_id
        self._last_eeg_features = {
            "theta": theta,
            "alpha": alpha,
            "beta": beta,
            "engagement": engagement,
            "workload": workload,
            "approach_avoidance": approach_avoidance,
            "approach_avoidance_available": approach_avoidance_available,
            "electrode_names": electrode_names,
        }
        label, severity, reason = classify_state(engagement, workload, self._last_activity)
        self.database.execute(
            """
            INSERT INTO events (session_id, started_at, ended_at, label, severity, reason, related_observation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, started_at, ended_at, label, severity, reason, None),
        )
        return window_id

    def add_screen_observation(self, image_path: Path, source_name: str) -> int:
        session_id = self.require_active()
        session = self.database.query_one("SELECT todo FROM sessions WHERE id = ?", (session_id,))
        todo = str(session["todo"] if session else "")
        analysis = self.gemini.analyze_screen(image_path, source_name, todo)
        observation_id = self.database.execute(
            """
            INSERT INTO screen_observations
            (session_id, captured_at, source_name, image_path, description, ocr_text, privacy_state)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                now_iso(),
                source_name,
                str(image_path),
                analysis.description,
                analysis.ocr_text,
                analysis.privacy_state,
            ),
        )
        if analysis.privacy_state in {"sent", "local_only", "error"}:
            self.rag.add(
                f"画面観測: {analysis.description}",
                {"type": "screen", "source_name": source_name, "privacy_state": analysis.privacy_state},
                f"session:{session_id}:screen:{observation_id}",
                session_id,
            )
        self._last_observation_id = observation_id
        self._last_observation_description = analysis.description
        return observation_id

    def add_episode(
        self,
        started_at: str,
        ended_at: str,
        active_window: str,
        activity: ActivityWindow,
        observation_id: int | None = None,
        screen_description: str | None = None,
    ) -> dict[str, object]:
        session_id = self.require_active()
        session = self.database.query_one("SELECT todo FROM sessions WHERE id = ?", (session_id,))
        todo = str(session["todo"] if session else "")
        self._last_activity = activity
        activity_id = self.add_activity(started_at, ended_at, activity)
        eeg_features = self._last_eeg_features or {
            "theta": 0.0,
            "alpha": 0.0,
            "beta": 0.0,
            "engagement": 0.0,
            "workload": 0.0,
            "approach_avoidance": 0.0,
            "approach_avoidance_available": False,
            "electrode_names": [],
        }
        label, severity, reason = classify_state(
            float(eeg_features.get("engagement", 0.0)),
            float(eeg_features.get("workload", 0.0)),
            activity,
        )
        description = screen_description if screen_description is not None else self._last_observation_description
        related_observation_id = (
            observation_id
            if observation_id is not None
            else (None if screen_description is not None else self._last_observation_id)
        )
        work_summary = self.gemini.summarize_episode(
            todo,
            active_window,
            description,
            eeg_features,
            {
                "key_count": activity.key_count,
                "mouse_distance": activity.mouse_distance,
                "click_count": activity.click_count,
                "scroll_count": activity.scroll_count,
                "idle_seconds": activity.idle_seconds,
            },
        )
        embedding_ref = self.rag.add(
            work_summary,
            {"type": "episode", "label": label, "active_window": active_window},
            f"session:{session_id}:episode:{started_at}",
            session_id,
        )
        episode_id = self.database.execute(
            """
            INSERT INTO episodes
            (session_id, started_at, ended_at, todo, active_window, observation_id, eeg_window_id,
             activity_id, label, severity, work_summary, embedding_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                started_at,
                ended_at,
                todo,
                active_window,
                related_observation_id,
                self._last_eeg_window_id,
                activity_id,
                label,
                severity,
                work_summary,
                embedding_ref,
            ),
        )
        self.database.execute(
            """
            INSERT INTO events (session_id, started_at, ended_at, label, severity, reason, related_observation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                started_at,
                ended_at,
                label,
                severity,
                reason,
                related_observation_id,
            ),
        )
        return {
            "episode_id": episode_id,
            "label": label,
            "severity": severity,
            "work_summary": work_summary,
            "embedding_ref": embedding_ref,
        }

    def current(self, requested_session_id: str | None = None) -> dict[str, object]:
        session_id = requested_session_id or self.active_session_id
        if not session_id and not requested_session_id:
            latest = self.database.query_one("SELECT * FROM sessions ORDER BY started_at DESC LIMIT 1")
            session_id = str(latest["id"]) if latest else None
        if not session_id:
            return {
                "active": False,
                "session": None,
                "eeg_windows": [],
                "activity_windows": [],
                "normalization_baseline": self._normalization_baseline(None),
                "observations": [],
                "events": [],
                "episodes": [],
                "phases": [],
                "reports": [],
            }
        session = self.database.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if not session:
            return {
                "active": False,
                "session": None,
                "eeg_windows": [],
                "activity_windows": [],
                "normalization_baseline": self._normalization_baseline(None),
                "observations": [],
                "events": [],
                "episodes": [],
                "phases": [],
                "reports": [],
            }
        eeg_limit = 10_000 if requested_session_id else CURRENT_EEG_LIMIT
        activity_limit = 10_000 if requested_session_id else CURRENT_ACTIVITY_LIMIT
        event_limit = 10_000 if requested_session_id else CURRENT_EVENT_LIMIT
        episode_limit = 10_000 if requested_session_id else CURRENT_EPISODE_LIMIT
        observation_limit = 1_000 if requested_session_id else 10
        eeg = self.database.query(
            """
            SELECT * FROM (
                SELECT * FROM eeg_windows WHERE session_id = ? ORDER BY started_at DESC LIMIT ?
            ) ORDER BY started_at
            """,
            (session_id, eeg_limit),
        )
        for window in eeg:
            window["approach_avoidance_available"] = bool(window.get("approach_avoidance_available"))
            window["electrode_names"] = from_json(str(window.pop("electrode_names_json", "[]")), [])
        observations = self.database.query(
            "SELECT * FROM screen_observations WHERE session_id = ? ORDER BY captured_at DESC LIMIT ?",
            (session_id, observation_limit),
        )
        activity = self.database.query(
            """
            SELECT * FROM (
                SELECT * FROM input_activity WHERE session_id = ? ORDER BY started_at DESC LIMIT ?
            ) ORDER BY started_at
            """,
            (session_id, activity_limit),
        )
        events = self.database.query(
            """
            SELECT * FROM (
                SELECT * FROM events WHERE session_id = ? ORDER BY started_at DESC LIMIT ?
            ) ORDER BY started_at
            """,
            (session_id, event_limit),
        )
        episodes = self.database.query(
            """
            SELECT * FROM (
                SELECT * FROM episodes WHERE session_id = ? ORDER BY started_at DESC LIMIT ?
            ) ORDER BY started_at
            """,
            (session_id, episode_limit),
        )
        phases = self.database.query("SELECT * FROM task_phases WHERE session_id = ? ORDER BY started_at", (session_id,))
        for phase in phases:
            phase["episode_ids"] = from_json(str(phase.pop("episode_ids_json")), [])
            phase["completed"] = bool(phase["completed"])
        reports = self.database.query(
            "SELECT * FROM reports WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, CURRENT_REPORT_LIMIT),
        )
        for report in reports:
            report["todo_suggestions"] = from_json(str(report.pop("todo_suggestions_json")), [])
        return {
            "active": self.active_session_id == session_id,
            "session": session,
            "eeg_windows": eeg,
            "activity_windows": activity,
            "normalization_baseline": self._normalization_baseline(session_id),
            "observations": observations,
            "events": events,
            "episodes": episodes,
            "phases": phases,
            "reports": reports,
        }

    def require_active(self) -> str:
        if not self.active_session_id:
            raise RuntimeError("No active session")
        return self.active_session_id

    def _normalization_baseline(self, current_session_id: str | None) -> dict[str, object]:
        previous = self.database.query_one(
            """
            SELECT id FROM sessions
            WHERE (? IS NULL OR id != ?) AND status = ?
            ORDER BY COALESCE(ended_at, started_at) DESC
            LIMIT 1
            """,
            (current_session_id, current_session_id, "stopped"),
        )
        previous_session_id = str(previous["id"]) if previous else None
        return {
            "source_session_id": previous_session_id,
            "eeg": self._metric_stats("eeg_windows", EEG_NORMALIZATION_COLUMNS, previous_session_id),
            "activity": self._metric_stats("input_activity", ACTIVITY_NORMALIZATION_COLUMNS, previous_session_id),
        }

    def _metric_stats(
        self,
        table: str,
        columns: tuple[str, ...],
        session_id: str | None,
    ) -> dict[str, dict[str, float | int]]:
        if not session_id:
            return {}
        expressions = []
        for column in columns:
            expressions.extend(
                [
                    f"AVG({column}) AS {column}_mean",
                    f"AVG({column} * {column}) AS {column}_mean_square",
                    f"COUNT({column}) AS {column}_count",
                ],
            )
        row = self.database.query_one(
            f"SELECT {', '.join(expressions)} FROM {table} WHERE session_id = ?",
            (session_id,),
        )
        if not row:
            return {}
        stats: dict[str, dict[str, float | int]] = {}
        for column in columns:
            count = int(row.get(f"{column}_count") or 0)
            mean_value = row.get(f"{column}_mean")
            mean_square_value = row.get(f"{column}_mean_square")
            if count == 0 or mean_value is None or mean_square_value is None:
                continue
            mean = float(mean_value)
            variance = max(0.0, float(mean_square_value) - mean * mean)
            stats[column] = {"mean": mean, "variance": variance, "count": count}
        return stats
