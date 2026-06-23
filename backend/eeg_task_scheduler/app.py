from __future__ import annotations

import argparse
import asyncio
import base64
import os
from pathlib import Path
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from .classifier import ActivityWindow
from .config import PROJECT_ROOT, SETTINGS
from .db import Database, from_json
from .eeg.ble import Ads1299BleClient
from .eeg.features import EegFeatureMonitor, synthetic_sample
from .eeg.protocol import DeviceConfig, EegSample
from .gemini_client import GeminiClient
from .notion_client import NotionClient
from .rag import RagStore
from .session import SessionService, now_iso

WEB_DIST = PROJECT_ROOT / "web" / "dist"
RUNTIME_PROTOCOL = "eeg-task-scheduler-runtime-v2"
SETTINGS.ensure_dirs()
database = Database(SETTINGS.data_dir / "app.sqlite3")
gemini = GeminiClient(SETTINGS.gemini_api_key, SETTINGS.gemini_model, SETTINGS.gemini_embedding_model)
notion = NotionClient(
    SETTINGS.notion_api_key,
    SETTINGS.notion_tasks_data_source_id,
    SETTINGS.notion_projects_data_source_id,
    SETTINGS.notion_version,
)
rag = RagStore(SETTINGS.data_dir, database, gemini)
sessions = SessionService(database, rag, gemini, SETTINGS.capture_dir)
feature_monitor = EegFeatureMonitor(
    sampling_rate=SETTINGS.sampling_rate,
    window_seconds=SETTINGS.eeg_window_seconds,
    step_seconds=SETTINGS.eeg_step_seconds,
    electrode_names=SETTINGS.eeg_electrode_names,
)


def handle_ble_message(message: DeviceConfig | EegSample) -> None:
    global last_persisted_feature_at
    if isinstance(message, DeviceConfig):
        feature_monitor.update_electrode_names(message.electrode_names)
        return
    if isinstance(message, EegSample) and sessions.active_session_id:
        status = feature_monitor.status()
        data = status.get("data")
        if data:
            ended_at = str(data.get("calculated_at", now_iso()))
            if ended_at == last_persisted_feature_at:
                return
            started_at = now_iso()
            try:
                sessions.add_eeg_window(started_at, ended_at, data, status.get("signal_quality", []))
                last_persisted_feature_at = ended_at
            except RuntimeError:
                pass


ble_client = Ads1299BleClient(
    SETTINGS.device_name,
    handle_ble_message,
    feature_monitor=feature_monitor,
    device_address=SETTINGS.device_address,
)
ble_task: asyncio.Task[None] | None = None
mock_index = 0
last_persisted_feature_at: str | None = None

app = FastAPI(title="EEG Task Scheduler")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8766"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-process-time-ms"],
)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    started_at = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
    response.headers["x-process-time-ms"] = str(elapsed_ms)
    return response


class StartSessionRequest(BaseModel):
    todo: str = "Review task"
    use_ble: bool = False
    notion_task_id: str | None = None
    notion_project_ids: list[str] = []


class ScreenObservationRequest(BaseModel):
    source_name: str
    image_base64: str


class ActivityRequest(BaseModel):
    started_at: str | None = None
    ended_at: str | None = None
    key_count: int = 0
    mouse_distance: float = 0.0
    click_count: int = 0
    scroll_count: int = 0
    idle_seconds: float = 0.0


class EpisodeRequest(BaseModel):
    started_at: str
    ended_at: str
    active_window: str
    observation_id: int | None = None
    screen_description: str | None = None
    key_count: int = 0
    mouse_distance: float = 0.0
    click_count: int = 0
    scroll_count: int = 0
    idle_seconds: float = 0.0


class NotionTaskCreateRequest(BaseModel):
    title: str
    project_ids: list[str] = []
    due: str | None = None
    status: str = "Not Started"
    priority: str | None = None
    note: str | None = None


class NotionStatusRequest(BaseModel):
    status: str


@app.get("/api/status")
async def status() -> dict[str, Any]:
    ble_status = ble_client.status()
    ble_status["features"] = await asyncio.to_thread(feature_monitor.status)
    capture_count = 0
    if sessions.active_session_id:
        capture_dir = SETTINGS.capture_dir / sessions.active_session_id
        capture_count = len(list(capture_dir.glob("*.png"))) if capture_dir.exists() else 0
    return {
        "ok": True,
        "session_active": sessions.active_session_id is not None,
        "session_id": sessions.active_session_id,
        "ble": ble_status,
        "gemini": {
            "available": gemini.available,
            "model": gemini.model,
            "embedding_model": gemini.embedding_model,
        },
        "capture": {
            "directory": str(SETTINGS.capture_dir),
            "interval_seconds": SETTINGS.observation_interval_seconds,
            "active_session_capture_count": capture_count,
        },
        "database": {"path": str(database.path), "ready": database.path.exists()},
        "rag": {"backend": "chroma" if rag.collection is not None else "sqlite-fallback"},
        "notion": notion.status(),
    }


@app.get("/api/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/runtime")
async def runtime() -> dict[str, object]:
    return {
        "ok": True,
        "protocol": RUNTIME_PROTOCOL,
        "pid": os.getpid(),
        "project_root": str(PROJECT_ROOT),
        "backend_url": f"http://{SETTINGS.host}:{SETTINGS.port}",
        "runtime_token": os.getenv("EEG_RUNTIME_TOKEN", ""),
    }


@app.get("/api/todos/initial")
async def initial_todo() -> dict[str, object]:
    try:
        notion_task = await asyncio.wait_for(asyncio.to_thread(notion.initial_todo), timeout=10.0)
    except Exception:
        notion_task = None
    if notion_task:
        return {
            "todo": notion_task.as_todo(),
            "source": "notion",
            "task": notion_task.__dict__,
        }
    latest = database.query_one("SELECT todo FROM sessions ORDER BY started_at DESC LIMIT 1")
    todo = str(latest["todo"]) if latest else "Review task"
    return {
        "todo": todo,
        "source": "local-adapter",
        "note": "Notion integration is optional; using the latest local task fallback.",
    }


@app.get("/api/todos/notion")
async def notion_todos() -> dict[str, object]:
    if not notion.configured:
        return {"configured": False, "tasks": [], "error": "Notion is not configured", "status": notion.status()}
    try:
        tasks = await asyncio.wait_for(asyncio.to_thread(notion.fetch_open_tasks, 50), timeout=12.0)
    except Exception as error:
        return {"configured": True, "tasks": [], "error": str(error), "status": notion.status()}
    return {
        "configured": True,
        "tasks": [task.__dict__ | {"todo": task.as_todo()} for task in tasks],
        "error": "",
        "status": notion.status(),
    }


@app.post("/api/todos/notion")
async def create_notion_task(request: NotionTaskCreateRequest) -> dict[str, object]:
    if not notion.configured:
        raise HTTPException(status_code=409, detail="Notion is not configured")
    task = await asyncio.to_thread(
        notion.create_task,
        request.title,
        tuple(request.project_ids),
        None,
        request.due,
        request.status,
        request.priority,
        request.note,
    )
    if not task:
        raise HTTPException(status_code=500, detail="Failed to create Notion task")
    return task.__dict__ | {"todo": task.as_todo()}


@app.patch("/api/todos/notion/{page_id}/status")
async def update_notion_task_status(page_id: str, request: NotionStatusRequest) -> dict[str, object]:
    if not notion.configured:
        raise HTTPException(status_code=409, detail="Notion is not configured")
    ok = await asyncio.to_thread(notion.update_task_status, page_id, request.status)
    return {"ok": ok, "page_id": page_id, "status": request.status}


@app.post("/api/ble/connect")
async def connect_ble() -> dict[str, Any]:
    global ble_task
    if ble_task and not ble_task.done():
        raise HTTPException(status_code=409, detail="BLE connection is already in progress")
    ble_task = asyncio.create_task(ble_client.start())
    try:
        await ble_task
    except Exception as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    finally:
        ble_task = None
    return ble_client.status()


@app.post("/api/ble/disconnect")
async def disconnect_ble() -> dict[str, Any]:
    global ble_task
    if ble_task and not ble_task.done():
        ble_task.cancel()
        ble_task = None
    await ble_client.stop()
    return ble_client.status()


@app.post("/api/session/start")
async def start_session(request: StartSessionRequest) -> dict[str, str]:
    global last_persisted_feature_at
    feature_monitor.reset()
    last_persisted_feature_at = None
    if request.use_ble and not ble_client.streaming:
        raise HTTPException(status_code=409, detail="Connect BLE before starting a BLE session")
    session_id = sessions.start(
        request.todo,
        {
            "session_minutes": SETTINGS.session_minutes,
            "use_ble": request.use_ble,
            "observation_interval_seconds": SETTINGS.observation_interval_seconds,
            "notion_task_id": request.notion_task_id,
            "notion_project_ids": request.notion_project_ids,
        },
    )
    if request.notion_task_id and notion.configured:
        await asyncio.to_thread(notion.update_task_status, request.notion_task_id, "In Progress")
    return {"session_id": session_id}


@app.post("/api/session/stop")
async def stop_session() -> dict[str, object]:
    try:
        return await asyncio.to_thread(sessions.stop)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/session/abort")
async def abort_session() -> dict[str, object]:
    try:
        return sessions.abort()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/reports/{report_id}/apply-notion")
async def apply_report_to_notion(report_id: int) -> dict[str, object]:
    if not notion.configured:
        raise HTTPException(status_code=409, detail="Notion is not configured")
    report = database.query_one("SELECT * FROM reports WHERE id = ?", (report_id,))
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    session = database.query_one("SELECT * FROM sessions WHERE id = ?", (report["session_id"],))
    settings = from_json(str(session.get("settings_json", "{}")), {}) if session else {}
    project_ids = tuple(settings.get("notion_project_ids") or ())
    source_task_id = settings.get("notion_task_id")
    suggestions = from_json(str(report.get("todo_suggestions_json", "[]")), [])
    phases = database.query("SELECT * FROM task_phases WHERE session_id = ? ORDER BY started_at", (report["session_id"],))
    phase_next_tasks = [str(phase.get("next_task", "")).strip() for phase in phases if str(phase.get("next_task", "")).strip()]
    titles = phase_next_tasks or [str(suggestion).strip() for suggestion in suggestions if str(suggestion).strip()]

    created = []
    for title in titles:
        task = await asyncio.to_thread(
            notion.create_task,
            str(title).strip(),
            project_ids,
            str(source_task_id) if source_task_id else None,
            None,
            "Not Started",
            None,
            f"Generated from EEG Pomodoro report {report_id}.",
        )
        if task:
            created.append(task.__dict__ | {"todo": task.as_todo()})

    if source_task_id:
        await asyncio.to_thread(
            notion.add_comment,
            str(source_task_id),
            f"EEG Pomodoro report:\n{report['summary']}",
        )
    database.execute("UPDATE reports SET approval_state = ? WHERE id = ?", ("synced_to_notion", report_id))
    return {"created_tasks": created, "commented_source_task": bool(source_task_id)}


@app.get("/api/session/current")
async def current_session() -> dict[str, object]:
    return sessions.current()


@app.get("/api/session/{session_id}")
async def session_by_id(session_id: str) -> dict[str, object]:
    result = sessions.current(session_id)
    if result["session"] is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@app.post("/api/observations/screen")
async def add_screen_observation(request: ScreenObservationRequest) -> dict[str, object]:
    try:
        session_id = sessions.require_active()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    image_bytes = base64.b64decode(request.image_base64.split(",")[-1])
    session_dir = SETTINGS.capture_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    image_path = session_dir / f"{now_iso().replace(':', '').replace('.', '-')}.png"
    image_path.write_bytes(image_bytes)
    observation_id = sessions.add_screen_observation(image_path, request.source_name)
    return {"observation_id": observation_id, "image_path": str(image_path)}


@app.post("/api/input/activity")
async def add_activity(request: ActivityRequest) -> dict[str, int]:
    try:
        activity_id = sessions.add_activity(
            request.started_at or now_iso(),
            request.ended_at or now_iso(),
            ActivityWindow(
                key_count=request.key_count,
                mouse_distance=request.mouse_distance,
                click_count=request.click_count,
                scroll_count=request.scroll_count,
                idle_seconds=request.idle_seconds,
            ),
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"activity_id": activity_id}


@app.post("/api/episodes")
async def add_episode(request: EpisodeRequest) -> dict[str, object]:
    try:
        result = sessions.add_episode(
            request.started_at,
            request.ended_at,
            request.active_window,
            ActivityWindow(
                key_count=request.key_count,
                mouse_distance=request.mouse_distance,
                click_count=request.click_count,
                scroll_count=request.scroll_count,
                idle_seconds=request.idle_seconds,
            ),
            request.observation_id,
            request.screen_description,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return result


@app.post("/api/mock/tick")
async def mock_tick() -> dict[str, object]:
    global mock_index
    if not sessions.active_session_id:
        sessions.start("Review task", {"mock": True})
    now = now_iso()
    sessions.add_activity(
        now,
        (Path(now).name if False else now),
        ActivityWindow(
            key_count=18 + (mock_index % 5),
            mouse_distance=900.0 if mock_index % 4 else 80.0,
            click_count=2,
            scroll_count=1,
            idle_seconds=60.0 if mock_index % 6 == 0 else 4.0,
        ),
    )
    for _ in range(round(SETTINGS.eeg_step_seconds * SETTINGS.sampling_rate)):
        sample = synthetic_sample(mock_index, SETTINGS.sampling_rate)
        mock_index += 1
        feature_monitor.add(sample)
    status_data = feature_monitor.status()
    data = status_data.get("data")
    if data:
        sessions.add_eeg_window(
            now,
            str(data.get("calculated_at", now)),
            data,
            status_data.get("signal_quality", []),
        )
    return await current_session()


@app.get("/api/captures/{session_id}/{filename}")
async def capture_file(session_id: str, filename: str) -> FileResponse:
    candidate = (SETTINGS.capture_dir / session_id / filename).resolve()
    root = SETTINGS.capture_dir.resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Capture not found")
    return FileResponse(candidate, media_type="image/png")


if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=SETTINGS.host)
    parser.add_argument("--port", type=int, default=SETTINGS.port)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        "eeg_task_scheduler.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(PROJECT_ROOT / "backend")] if args.reload else None,
    )


if __name__ == "__main__":
    main()

