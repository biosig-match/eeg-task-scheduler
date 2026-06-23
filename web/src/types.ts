export type StatusResponse = {
  ok: boolean;
  session_active: boolean;
  session_id: string | null;
  ble: {
    state: string;
    detail: string;
    streaming: boolean;
    receiving: boolean;
    device_name?: string;
    device_address?: string | null;
    last_scan_detail?: string;
    sample_count: number;
    features?: {
      ready: boolean;
      data: EegFeature | null;
      signal_quality: Array<Record<string, unknown>>;
    };
  };
  gemini: {
    available: boolean;
    model: string;
    embedding_model: string;
  };
  capture: {
    directory: string;
    interval_seconds: number;
    active_session_capture_count: number;
  };
  database: {
    path: string;
    ready: boolean;
  };
  rag: {
    backend: string;
  };
  notion?: {
    configured: boolean;
    tasks_data_source_id: boolean;
    projects_data_source_id: boolean;
    notion_version: string;
    last_error?: string;
  };
};

export type EegFeature = {
  calculated_at: string;
  theta: number;
  alpha: number;
  beta: number;
  engagement: number;
  workload: number;
  approach_avoidance: number;
  approach_avoidance_available: boolean;
  electrode_names: string[];
};

export type SessionRecord = {
  id: string;
  started_at: string;
  ended_at?: string | null;
  todo: string;
  status: string;
  settings_json: string;
};

export type EegWindowRecord = {
  id: number;
  started_at: string;
  ended_at: string;
  engagement: number;
  workload: number;
  theta: number;
  alpha: number;
  beta: number;
  approach_avoidance: number;
  approach_avoidance_available: boolean;
  electrode_names: string[];
};

export type ActivityWindowRecord = {
  id: number;
  started_at: string;
  ended_at: string;
  key_count: number;
  mouse_distance: number;
  click_count: number;
  scroll_count: number;
  idle_seconds: number;
};

export type NormalizationMetricStats = {
  mean: number;
  variance: number;
  count: number;
};

export type NormalizationBaseline = {
  source_session_id: string | null;
  eeg: Partial<Record<"engagement" | "workload" | "approach_avoidance", NormalizationMetricStats>>;
  activity: Partial<Record<"key_count" | "mouse_distance" | "click_count" | "scroll_count", NormalizationMetricStats>>;
};

export type ObservationRecord = {
  id: number;
  captured_at: string;
  source_name: string;
  image_path?: string | null;
  description: string;
  ocr_text: string;
  privacy_state: string;
};

export type EventRecord = {
  id: number;
  started_at: string;
  ended_at: string;
  label: string;
  severity: string;
  reason: string;
  related_observation_id?: number | null;
};

export type ReportRecord = {
  id: number;
  created_at: string;
  summary: string;
  todo_suggestions: string[];
  approval_state: string;
};

export type EpisodeRecord = {
  id: number;
  started_at: string;
  ended_at: string;
  todo: string;
  active_window: string;
  observation_id?: number | null;
  eeg_window_id?: number | null;
  activity_id?: number | null;
  label: string;
  severity: string;
  work_summary: string;
  embedding_ref: string;
};

export type TaskPhaseRecord = {
  id: number;
  started_at: string;
  ended_at: string;
  phase_type: string;
  title: string;
  summary: string;
  episode_ids: number[];
  completed: boolean;
  evidence: string;
};

export type SessionResponse = {
  active: boolean;
  session: SessionRecord | null;
  eeg_windows: EegWindowRecord[];
  activity_windows: ActivityWindowRecord[];
  normalization_baseline: NormalizationBaseline;
  observations: ObservationRecord[];
  events: EventRecord[];
  episodes: EpisodeRecord[];
  phases: TaskPhaseRecord[];
  reports: ReportRecord[];
};

declare global {
  interface Window {
    eegDesktop?: {
      listSources: () => Promise<Array<{ id: string; name: string; displayId?: string; thumbnail: string }>>;
      captureSource: (sourceId: string) => Promise<{ sourceId: string; dataUrl: string }>;
      capturePrimaryScreen: () => Promise<{ sourceId: string; sourceName: string; dataUrl: string }>;
      readGlobalInput: () => Promise<{
        key_count: number;
        mouse_distance: number;
        click_count: number;
        scroll_count: number;
      }>;
      getActiveWindow: () => Promise<{ title: string; processName: string }>;
      backendUrl: string;
      runtimeToken: string;
    };
  }
}
