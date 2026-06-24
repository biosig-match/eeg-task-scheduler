import type { SessionResponse, StatusResponse } from "./types";

import type { RagGraphResponse } from "./types";

const viteEnv = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
const backendUrl = window.eegDesktop?.backendUrl ?? viteEnv?.VITE_EEG_BACKEND_URL ?? "http://127.0.0.1:8766";
const expectedRuntimeToken = window.eegDesktop?.runtimeToken ?? viteEnv?.VITE_EEG_RUNTIME_TOKEN ?? "";
const runtimeProtocol = "eeg-task-scheduler-runtime-v2";

function emitApiTrace(detail: Record<string, unknown>) {
  window.dispatchEvent(new CustomEvent("api-trace", { detail }));
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 20_000, maxBytes?: number): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  const startedAt = performance.now();
  emitApiTrace({ path, phase: "start", method: init?.method ?? "GET" });
  try {
    const response = await fetch(`${backendUrl}${path}`, {
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      ...init,
      signal: controller.signal,
    });
    emitApiTrace({
      path,
      phase: "headers",
      status: response.status,
      backendMs: response.headers.get("x-process-time-ms") ?? "",
      elapsedMs: Math.round(performance.now() - startedAt),
    });
    const contentLength = Number(response.headers.get("content-length") ?? 0);
    if (maxBytes && contentLength > maxBytes) {
      throw new Error(`response too large: ${contentLength} bytes > ${maxBytes} bytes`);
    }
    const textStartedAt = performance.now();
    const text = await response.text();
    emitApiTrace({
      path,
      phase: "body",
      bytes: new Blob([text]).size,
      elapsedMs: Math.round(performance.now() - textStartedAt),
      totalMs: Math.round(performance.now() - startedAt),
    });
    const bodyBytes = new Blob([text]).size;
    if (maxBytes && bodyBytes > maxBytes) {
      throw new Error(`response too large: ${bodyBytes} bytes > ${maxBytes} bytes`);
    }
    if (!response.ok) {
      throw new Error(text || response.statusText);
    }
    emitApiTrace({ path, phase: "parse:start", bytes: new Blob([text]).size });
    const parsed = JSON.parse(text) as T;
    emitApiTrace({ path, phase: "parse:done", totalMs: Math.round(performance.now() - startedAt) });
    return parsed;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      emitApiTrace({ path, phase: "abort", elapsedMs: Math.round(performance.now() - startedAt) });
      throw new Error(`timeout after ${timeoutMs}ms`);
    }
    emitApiTrace({ path, phase: "error", error: String(error), elapsedMs: Math.round(performance.now() - startedAt) });
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

export const api = {
  assetUrl: (path: string) => `${backendUrl}${path}`,
  health: () => request<{ ok: boolean }>("/api/health", undefined, 5_000),
  runtime: () =>
    request<{
      ok: boolean;
      protocol: string;
      pid: number;
      project_root: string;
      backend_url: string;
      runtime_token: string;
    }>("/api/runtime", undefined, 5_000),
  expectedRuntimeProtocol: runtimeProtocol,
  expectedRuntimeToken,
  status: () => request<StatusResponse>("/api/status"),
  initialTodo: () =>
    request<{
      todo: string;
      source: string;
      note?: string;
      task?: { id: string; project_ids: string[]; project_names: string[] };
    }>("/api/todos/initial", undefined, 12_000),
  notionTasks: () =>
    request<{
      configured: boolean;
      error?: string;
      status?: {
        configured: boolean;
        tasks_data_source_id: boolean;
        projects_data_source_id: boolean;
        notion_version: string;
        last_error?: string;
      };
      tasks: Array<{
        id: string;
        title: string;
        status: string;
        project_ids: string[];
        project_names: string[];
        due: string | null;
        url: string;
        todo: string;
      }>;
    }>("/api/todos/notion", undefined, 15_000),
  current: () => request<SessionResponse>("/api/session/current", undefined, 20_000, 5_000_000),
  session: (sessionId: string) =>
    request<SessionResponse>(`/api/session/${encodeURIComponent(sessionId)}`, undefined, 20_000, 12_000_000),
  ragGraph: (sessionId?: string) =>
    request<RagGraphResponse>(
      `/api/rag/graph${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`,
      undefined,
      20_000,
      12_000_000,
    ),
  start: (todo: string, useBle: boolean, notionTaskId?: string, notionProjectIds: string[] = []) =>
    request<{ session_id: string }>("/api/session/start", {
      method: "POST",
      body: JSON.stringify({
        todo,
        use_ble: useBle,
        notion_task_id: notionTaskId,
        notion_project_ids: notionProjectIds,
      }),
    }),
  stop: () =>
    request<{ session_id: string; summary: string; todo_suggestions: string[] }>("/api/session/stop", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  abort: () =>
    request<{ session_id: string; summary: string; todo_suggestions: string[] }>("/api/session/abort", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  mockTick: () => request<SessionResponse>("/api/mock/tick", { method: "POST", body: JSON.stringify({}) }),
  connectBle: () => request<StatusResponse["ble"]>("/api/ble/connect", { method: "POST", body: JSON.stringify({}) }),
  disconnectBle: () => request<StatusResponse["ble"]>("/api/ble/disconnect", { method: "POST", body: JSON.stringify({}) }),
  addScreen: (sourceName: string, imageBase64: string) =>
    request<{ observation_id: number; image_path: string }>("/api/observations/screen", {
      method: "POST",
      body: JSON.stringify({ source_name: sourceName, image_base64: imageBase64 }),
    }),
  addEpisode: (payload: {
    started_at: string;
    ended_at: string;
    active_window: string;
    observation_id?: number | null;
    screen_description?: string;
    key_count: number;
    mouse_distance: number;
    click_count: number;
    scroll_count: number;
    idle_seconds: number;
  }) =>
    request<{ episode_id: number; label: string; severity: string; work_summary: string; embedding_ref: string }>(
      "/api/episodes",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  applyReportToNotion: (reportId: number) =>
    request<{ created_tasks: Array<{ id: string; todo: string }>; commented_source_task: boolean }>(
      `/api/reports/${reportId}/apply-notion`,
      {
        method: "POST",
        body: JSON.stringify({}),
      },
    ),
};

