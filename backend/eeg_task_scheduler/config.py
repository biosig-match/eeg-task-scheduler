from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


class Settings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8766
    data_dir: Path = PROJECT_ROOT / "data"
    capture_dir: Path = PROJECT_ROOT / "captures"
    recording_dir: Path = PROJECT_ROOT / "recordings"
    session_minutes: int = 25
    observation_interval_seconds: int = 30
    eeg_window_seconds: float = 10.0
    eeg_step_seconds: float = 5.0
    sampling_rate: float = 250.0
    eeg_electrode_names: tuple[str, ...] = ("C3", "Cz", "C4", "", "", "", "", "")
    device_name: str = "ADS1299_EEG_NUS"
    device_address: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    notion_api_key: str | None = None
    notion_tasks_data_source_id: str | None = None
    notion_projects_data_source_id: str | None = None
    notion_version: str = "2025-09-03"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.getenv("HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "8766")),
            data_dir=Path(os.getenv("DATA_DIR", PROJECT_ROOT / "data")),
            capture_dir=Path(os.getenv("CAPTURE_DIR", PROJECT_ROOT / "captures")),
            recording_dir=Path(os.getenv("RECORDING_DIR", PROJECT_ROOT / "recordings")),
            session_minutes=int(os.getenv("SESSION_MINUTES", "25")),
            observation_interval_seconds=int(os.getenv("OBSERVATION_INTERVAL_SECONDS", "30")),
            eeg_window_seconds=float(os.getenv("EEG_WINDOW_SECONDS", "10")),
            eeg_step_seconds=float(os.getenv("EEG_STEP_SECONDS", "5")),
            sampling_rate=float(os.getenv("EEG_SAMPLING_RATE", "250")),
            eeg_electrode_names=parse_electrode_names(
                os.getenv("EEG_ELECTRODE_NAMES") or os.getenv("EEG_CHANNEL_ELECTRODES")
            ),
            device_name=os.getenv("ADS1299_DEVICE_NAME", "ADS1299_EEG_NUS"),
            device_address=os.getenv("ADS1299_DEVICE_ADDRESS"),
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
            gemini_embedding_model=os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"),
            notion_api_key=os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN"),
            notion_tasks_data_source_id=os.getenv("NOTION_TASKS_DATA_SOURCE_ID") or os.getenv("NOTION_TASKS_DATABASE_ID"),
            notion_projects_data_source_id=os.getenv("NOTION_PROJECTS_DATA_SOURCE_ID")
            or os.getenv("NOTION_PROJECTS_DATABASE_ID"),
            notion_version=os.getenv("NOTION_VERSION", "2025-09-03"),
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.recording_dir.mkdir(parents=True, exist_ok=True)


def parse_electrode_names(value: str | None) -> tuple[str, ...]:
    if not value:
        return ("C3", "Cz", "C4", "", "", "", "", "")
    names = tuple(item.strip() for item in value.split(","))
    return names[:8] + ("",) * max(0, 8 - len(names))


SETTINGS = Settings.from_env()

