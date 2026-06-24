import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Camera,
  CameraOff,
  CheckCircle2,
  Network,
  Play,
  Radio,
  RefreshCw,
  Square,
} from "lucide-react";
import { api } from "./api";
import type {
  ActivityWindowRecord,
  EegWindowRecord,
  EventRecord,
  NormalizationMetricStats,
  ObservationRecord,
  RagGraphNode,
  RagGraphResponse,
  SessionResponse,
  StatusResponse,
} from "./types";
import "./styles.css";

const demoEvents: EventRecord[] = [
  {
    id: 1,
    started_at: toTokyoIsoString(new Date(Date.now() - 7 * 60_000)),
    ended_at: toTokyoIsoString(new Date(Date.now() - 5 * 60_000)),
    label: "フロー",
    severity: "good",
    reason: "操作量があり、負荷が過度ではない安定した集中区間として判定しました。",
  },
  {
    id: 2,
    started_at: toTokyoIsoString(new Date(Date.now() - 4 * 60_000)),
    ended_at: toTokyoIsoString(new Date(Date.now() - 2 * 60_000)),
    label: "過負荷停止",
    severity: "warning",
    reason: "認知負荷が高いまま操作量が落ちており、内容の難しさによる停滞の可能性があります。",
  },
];

type TaskOption = {
  id: string;
  todo: string;
  project_ids: string[];
  project_names: string[];
  status: string;
  due: string | null;
};

type BootState = {
  ready: boolean;
  label: string;
  detail: string;
  failed: boolean;
};

type BootTraceEntry = {
  at: string;
  label: string;
  detail: string;
};

function App() {
  const [bootState, setBootState] = useState<BootState>({
    ready: false,
    label: "Starting",
    detail: "Waiting for the local backend API.",
    failed: false,
  });
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [todo, setTodo] = useState("Q2事業レビュー資料 - 3. 考察を書く");
  const [sources, setSources] = useState<Array<{ id: string; name: string; displayId?: string; thumbnail: string }>>([]);
  const [sourceId, setSourceId] = useState<string>("");
  const [message, setMessage] = useState<string>("");
  const [autoRunning, setAutoRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [bleActionPending, setBleActionPending] = useState(false);
  const [notionTaskId, setNotionTaskId] = useState<string | undefined>();
  const [notionProjectIds, setNotionProjectIds] = useState<string[]>([]);
  const [taskOptions, setTaskOptions] = useState<TaskOption[]>([]);
  const [notionTasksLoading, setNotionTasksLoading] = useState(false);
  const [notionTasksError, setNotionTasksError] = useState("");
  const [notionApplyMessage, setNotionApplyMessage] = useState("");
  const [notionApplyPending, setNotionApplyPending] = useState(false);
  const [bootTrace, setBootTrace] = useState<BootTraceEntry[]>([]);
  const [heartbeat, setHeartbeat] = useState({ frames: 0, lastGapMs: 0 });
  const [scrubRatio, setScrubRatio] = useState(0);
  const [screenshotEnabled, setScreenshotEnabled] = useState(true);
  const [view, setView] = useState<"session" | "rag-graph">("session");
  const [ragGraph, setRagGraph] = useState<RagGraphResponse | null>(null);
  const [ragGraphLoading, setRagGraphLoading] = useState(false);
  const [ragGraphError, setRagGraphError] = useState("");
  const [selectedGraphNodeId, setSelectedGraphNodeId] = useState<string | null>(null);
  const [graphRotation, setGraphRotation] = useState({ x: -0.45, y: 0.72 });
  const lastEpisodeAtRef = useRef(0);
  const lastActiveWindowRef = useRef("");
  const mockEegActiveRef = useRef(false);
  const captureInFlightRef = useRef(false);
  const sessionActiveRef = useRef(false);
  const sourceIdRef = useRef("");
  const screenshotEnabledRef = useRef(true);
  const sourcesRef = useRef<Array<{ id: string; name: string; displayId?: string; thumbnail: string }>>([]);
  const episodeStartRef = useRef(toTokyoIsoString());
  const previewSessionId = useMemo(() => new URLSearchParams(window.location.search).get("session_id") ?? "", []);
  const graphSessionId = previewSessionId || session?.session?.id || "20260623-132529-ebe052";
  const autoScrubRef = useRef(true);
  const lastSessionIdRef = useRef<string | null>(null);
  const activityRef = useRef({
    key_count: 0,
    mouse_distance: 0,
    click_count: 0,
    scroll_count: 0,
    lastMouseX: null as number | null,
    lastMouseY: null as number | null,
  });

  const appendTrace = (label: string, detail = "") => {
    const entry = {
      at: new Date().toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
      label,
      detail,
    };
    setBootTrace((items) => [...items.slice(-17), entry]);
  };

  const refresh = async () => {
    appendTrace("refresh:start");
    const [nextStatus, nextSession] = await Promise.all([
      api.status(),
      previewSessionId ? api.session(previewSessionId) : api.current(),
    ]);
    appendTrace(
      "refresh:fetched",
      `eeg=${nextSession.eeg_windows.length} events=${nextSession.events.length} observations=${nextSession.observations.length}`,
    );
    setStatus(nextStatus);
    setSession(nextSession);
    sessionActiveRef.current = previewSessionId ? false : nextSession.active;
    appendTrace("refresh:state-set", `active=${nextSession.active}`);
  };

  const loadRagGraph = async () => {
    setRagGraphLoading(true);
    setRagGraphError("");
    try {
      const graph = await api.ragGraph(graphSessionId);
      setRagGraph(graph);
      setSelectedGraphNodeId((current) => current ?? graph.nodes[0]?.id ?? null);
    } catch (error) {
      setRagGraph(null);
      setRagGraphError(String(error));
    } finally {
      setRagGraphLoading(false);
    }
  };

  const waitForBackendReady = async () => {
    const startedAt = Date.now();
    while (Date.now() - startedAt < 45_000) {
      try {
        const runtime = await api.runtime();
        const tokenMatches = !api.expectedRuntimeToken || runtime.runtime_token === api.expectedRuntimeToken;
        if (runtime.protocol === api.expectedRuntimeProtocol && tokenMatches) {
          appendTrace("backend:runtime", `pid=${runtime.pid} protocol=${runtime.protocol}`);
          return true;
        }
        appendTrace("backend:runtime-mismatch", `protocol=${runtime.protocol} token=${tokenMatches ? "ok" : "stale"}`);
        await delay(500);
        continue;
      } catch (error) {
        appendTrace("backend:runtime-wait", String(error));
        await delay(500);
      }
    }
    return false;
  };

  const loadNotionTasks = async () => {
    setNotionTasksLoading(true);
    setNotionTasksError("");
    try {
      const result = await api.notionTasks();
      setTaskOptions(result.tasks.slice(0, 30));
      if (result.error) {
        setNotionTasksError(result.error);
      } else if (!result.configured) {
        setNotionTasksError("Notion is not configured");
      }
    } catch (error) {
      setTaskOptions([]);
      setNotionTasksError(String(error));
    } finally {
      setNotionTasksLoading(false);
    }
  };

  useEffect(() => {
    const onApiTrace = (event: Event) => {
      const detail = (event as CustomEvent<Record<string, unknown>>).detail;
      const path = String(detail.path ?? "");
      const phase = String(detail.phase ?? "");
      const bytes = typeof detail.bytes === "number" ? ` bytes=${detail.bytes}` : "";
      const elapsed = typeof detail.elapsedMs === "number" ? ` elapsed=${detail.elapsedMs}ms` : "";
      const backend = detail.backendMs ? ` backend=${detail.backendMs}ms` : "";
      const total = typeof detail.totalMs === "number" ? ` total=${detail.totalMs}ms` : "";
      const statusCode = typeof detail.status === "number" ? ` status=${detail.status}` : "";
      appendTrace(`api:${phase}`, `${path}${statusCode}${backend}${bytes}${elapsed}${total}`);
    };
    window.addEventListener("api-trace", onApiTrace);
    return () => window.removeEventListener("api-trace", onApiTrace);
  }, []);

  useEffect(() => {
    let frame = 0;
    let last = performance.now();
    let animationId = 0;
    let disposed = false;
    const tickFrame = (now: number) => {
      const gap = now - last;
      last = now;
      frame += 1;
      if (gap > 500) {
        appendTrace("renderer:long-frame", `${Math.round(gap)}ms`);
      }
      if (frame % 30 === 0) {
        setHeartbeat({ frames: frame, lastGapMs: Math.round(gap) });
      }
      if (!disposed) {
        animationId = window.requestAnimationFrame(tickFrame);
      }
    };
    animationId = window.requestAnimationFrame(tickFrame);
    return () => {
      disposed = true;
      window.cancelAnimationFrame(animationId);
    };
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: number | undefined;
    const boot = async () => {
      appendTrace("boot:start");
      setBootState({
        ready: false,
        label: "Starting backend",
        detail: "Checking that the local API is ready before enabling controls.",
        failed: false,
      });
      const ready = await waitForBackendReady();
      appendTrace("backend:ready-check", `ready=${ready}`);
      if (disposed) return;
      if (!ready) {
        setBootState({
          ready: false,
          label: "Backend is not ready",
          detail: "Restart npm run dev, or check the backend terminal output.",
          failed: true,
        });
        setMessage("Backend API is not ready. Please restart npm run dev.");
        setNotionTasksError("Backend API is not ready");
        return;
      }
      setBootState({
        ready: false,
        label: "Loading session",
        detail: "Reading session, BLE, database, and task status.",
        failed: false,
      });
      appendTrace("session:load:start");
      await refresh().catch((error) => setMessage(String(error)));
      appendTrace("session:load:done");
      if (disposed) return;
      timer = window.setInterval(() => refresh().catch(() => undefined), 2500);
      setBootState({
        ready: false,
        label: "Loading tasks",
        detail: "Preparing task choices so the first interaction is reliable.",
        failed: false,
      });
      appendTrace("tasks:load:start");
      await Promise.allSettled([
        api.initialTodo().then((result) => {
          if (disposed) return;
          setTodo(result.todo);
          setNotionTaskId(result.task?.id);
          setNotionProjectIds(result.task?.project_ids ?? []);
        }),
        loadNotionTasks(),
      ]);
      appendTrace("tasks:load:done");
      if (disposed) return;
      setBootState({
        ready: true,
        label: "Ready",
        detail: "Controls are available.",
        failed: false,
      });
      appendTrace("boot:ready");
    };
    boot().catch((error) => setMessage(String(error)));
    const desktop = window.eegDesktop;
    if (desktop?.listSources) {
      desktop
        .listSources()
        .then((nextSources) => {
          setSources(nextSources);
          const screenSource = nextSources.find((source) => source.id.startsWith("screen:"));
          setSourceId((screenSource ?? nextSources[0])?.id ?? "");
        })
        .catch((error) => setMessage(`キャプチャ対象の取得に失敗しました: ${String(error)}`));
    }
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const onKey = () => {
      activityRef.current.key_count += 1;
    };
    const onClick = () => {
      activityRef.current.click_count += 1;
    };
    const onWheel = () => {
      activityRef.current.scroll_count += 1;
    };
    const onMove = (event: MouseEvent) => {
      const lastX = activityRef.current.lastMouseX;
      const lastY = activityRef.current.lastMouseY;
      if (lastX !== null && lastY !== null) {
        activityRef.current.mouse_distance += Math.hypot(event.clientX - lastX, event.clientY - lastY);
      }
      activityRef.current.lastMouseX = event.clientX;
      activityRef.current.lastMouseY = event.clientY;
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("click", onClick);
    window.addEventListener("wheel", onWheel);
    window.addEventListener("mousemove", onMove);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("click", onClick);
      window.removeEventListener("wheel", onWheel);
      window.removeEventListener("mousemove", onMove);
    };
  }, []);

  useEffect(() => {
    if (!autoRunning) return;
    const timer = window.setInterval(() => {
      runAutoEpisode(false).catch((error) => setMessage(String(error)));
    }, 5000);
    return () => window.clearInterval(timer);
  }, [autoRunning]);

  useEffect(() => {
    sourceIdRef.current = sourceId;
  }, [sourceId]);

  useEffect(() => {
    screenshotEnabledRef.current = screenshotEnabled;
  }, [screenshotEnabled]);

  useEffect(() => {
    sourcesRef.current = sources;
  }, [sources]);

  const events = session?.events.length ? session.events : demoEvents;
  const observations = session?.observations ?? [];
  const eeg = session?.eeg_windows ?? [];
  const activity = session?.activity_windows ?? [];
  const timelineRange = useMemo(() => sessionTimeRange(session, eeg, activity), [session, eeg, activity]);
  const scrubbedAt = timelineRange.min + scrubRatio * Math.max(1, timelineRange.max - timelineRange.min);
  const scrubEvent = eventAt(events, scrubbedAt);
  const scrubObservation = nearestObservation(observations, scrubbedAt);
  const scrubCaptureUrl = captureUrl(session?.session?.id, scrubObservation);
  const scrubSummary = observationSummary(scrubObservation);
  const bleState = status?.ble.state ?? "unknown";
  const bleBusy = bleActionPending || ["scanning", "connecting", "subscribing", "starting", "disconnecting"].includes(bleState);
  const bleConnected = Boolean(status?.ble.streaming);
  const bleButtonClass = ["icon-button", "ble-button", bleConnected ? "connected" : "", bleBusy ? "busy" : ""]
    .filter(Boolean)
    .join(" ");
  const taskSelectStatus =
    notionTasksLoading
      ? "Notion tasks loading..."
      : notionTasksError
        ? `Notion error: ${notionTasksError}`
        : taskOptions.length
          ? ""
          : "Notion taskなし";
  const taskSelectValue = notionTaskId ?? (taskSelectStatus ? "__notion_status" : "");
  const controlsLocked = !bootState.ready;

  useEffect(() => {
    const sessionId = session?.session?.id ?? null;
    if (lastSessionIdRef.current !== sessionId) {
      lastSessionIdRef.current = sessionId;
      autoScrubRef.current = true;
      if (previewSessionId) {
        setScrubRatio(0);
      }
    }
  }, [previewSessionId, session?.session?.id]);

  useEffect(() => {
    if (previewSessionId || !autoScrubRef.current) return;
    const targetAt = liveScrubTimestamp(timelineRange, eeg, activity, events, observations);
    setScrubRatio(ratioInRange(targetAt, timelineRange));
  }, [previewSessionId, timelineRange, eeg, activity, events, observations]);

  useEffect(() => {
    if (view !== "rag-graph" || controlsLocked) return;
    loadRagGraph().catch((error) => setRagGraphError(String(error)));
  }, [view, controlsLocked, graphSessionId]);

  const start = async () => {
    setMessage("");
    let useBle = true;
    try {
      await api.connectBle();
    } catch (error) {
      useBle = false;
      setMessage(`BLE接続に失敗したため，モックEEGで開始します: ${String(error)}`);
    }
    await api.start(todo, useBle, notionTaskId, notionProjectIds);
    mockEegActiveRef.current = !useBle;
    if (!useBle) {
      await api.mockTick();
    }
    if (window.eegDesktop) {
      await withTimeout(
        window.eegDesktop.readGlobalInput(),
        3000,
        { key_count: 0, mouse_distance: 0, click_count: 0, scroll_count: 0 },
      );
    }
    sessionActiveRef.current = true;
    setAutoRunning(true);
    lastEpisodeAtRef.current = 0;
    lastActiveWindowRef.current = "";
    episodeStartRef.current = toTokyoIsoString();
    await refresh();
    await runAutoEpisode(true);
  };

  const stop = async () => {
    setMessage("");
    setAutoRunning(false);
    sessionActiveRef.current = false;
    mockEegActiveRef.current = false;
    setStopping(true);
    try {
      const report = await withTimeout(api.stop(), 20_000);
      setMessage(report.summary);
    } catch (error) {
      const aborted = await api.abort();
      setMessage(`停止しました。レポート生成は完了しませんでした: ${String(error)} / ${aborted.summary}`);
    } finally {
      setStopping(false);
      await refresh();
    }
  };

  const toggleBle = async () => {
    setMessage("");
    setBleActionPending(true);
    try {
      if (status?.ble.streaming || ["scanning", "connecting", "subscribing", "starting"].includes(status?.ble.state ?? "")) {
        await api.disconnectBle();
      } else {
        await api.connectBle();
      }
      await refresh();
    } catch (error) {
      setMessage(`BLE操作に失敗しました: ${String(error)}`);
      await refresh().catch(() => undefined);
    } finally {
      setBleActionPending(false);
    }
  };

  const runAutoEpisode = async (force: boolean) => {
    if (captureInFlightRef.current) return;
    if (!force && !sessionActiveRef.current) return;
    if (!window.eegDesktop) {
      setMessage("ブラウザ表示では自動スクリーンショットは使えません。Electronの npm run dev / npm start から起動してください。");
      return;
    }
    captureInFlightRef.current = true;
    try {
      if (mockEegActiveRef.current) {
        await api.mockTick().catch(() => undefined);
      }
      const active = await withTimeout(
        window.eegDesktop.getActiveWindow(),
        3000,
        { title: "active-window-timeout", processName: "" },
      );
      const activeWindow = [active.processName, active.title].filter(Boolean).join(" - ") || "unknown";
      const nowMs = Date.now();
      const switched = activeWindow !== lastActiveWindowRef.current;
      if (!force && !switched && nowMs - lastEpisodeAtRef.current < 30_000) {
        return;
      }
      const startedAt = episodeStartRef.current;
      const endedAt = toTokyoIsoString();
      let observationId: number | null = null;
      let screenDescription: string | undefined;
      if (screenshotEnabledRef.current) {
        const shot = await captureSelectedScreen();
        const observationSource = [shot.sourceName, activeWindow].filter(Boolean).join(" | ");
        const observation = await withTimeout(api.addScreen(observationSource || shot.sourceName, shot.dataUrl), 20_000);
        observationId = observation.observation_id;
      } else {
        screenDescription = "スクリーンショット記録は無効です。";
      }
      const activity = await collectActivitySnapshot();
      const idleSeconds = Math.max(0, (nowMs - lastEpisodeAtRef.current) / 1000);
      await withTimeout(api.addEpisode({
        started_at: startedAt,
        ended_at: endedAt,
        active_window: activeWindow,
        observation_id: observationId,
        screen_description: screenDescription,
        key_count: activity.key_count,
        mouse_distance: Math.round(activity.mouse_distance),
        click_count: activity.click_count,
        scroll_count: activity.scroll_count,
        idle_seconds: lastEpisodeAtRef.current === 0 ? 0 : Math.min(120, idleSeconds),
      }), 30_000);
      activityRef.current = {
        key_count: 0,
        mouse_distance: 0,
        click_count: 0,
        scroll_count: 0,
        lastMouseX: activityRef.current.lastMouseX,
        lastMouseY: activityRef.current.lastMouseY,
      };
      lastEpisodeAtRef.current = nowMs;
      lastActiveWindowRef.current = activeWindow;
      episodeStartRef.current = endedAt;
      await refresh();
      const startedAtMs = session?.session?.started_at ? Date.parse(session.session.started_at) : 0;
      if (startedAtMs && nowMs - startedAtMs >= 25 * 60_000) {
        await stop();
      }
    } catch (error) {
      setMessage(`キャプチャ対象の取得に失敗しました: ${String(error)}`);
    } finally {
      captureInFlightRef.current = false;
    }
  };

  const collectActivitySnapshot = async () => {
    const localActivity = activityRef.current;
    const globalActivity = window.eegDesktop
      ? await withTimeout(
          window.eegDesktop.readGlobalInput(),
          3000,
          { key_count: 0, mouse_distance: 0, click_count: 0, scroll_count: 0 },
        )
      : { key_count: 0, mouse_distance: 0, click_count: 0, scroll_count: 0 };
    return {
      key_count: localActivity.key_count + globalActivity.key_count,
      mouse_distance: localActivity.mouse_distance + globalActivity.mouse_distance,
      click_count: localActivity.click_count + globalActivity.click_count,
      scroll_count: localActivity.scroll_count + globalActivity.scroll_count,
    };
  };

  const captureSelectedScreen = async () => {
    if (!window.eegDesktop) throw new Error("Electron API is not available");
    const nextSources = sourcesRef.current.length ? sourcesRef.current : await withTimeout(window.eegDesktop.listSources(), 8000);
    if (!sourcesRef.current.length) {
      setSources(nextSources);
    }
    const selectedSource =
      nextSources.find((source) => source.id === sourceIdRef.current) ??
      nextSources.find((source) => source.id.startsWith("screen:")) ??
      nextSources[0];
    if (!selectedSource) throw new Error("No capture source is available");
    if (selectedSource.id !== sourceIdRef.current) {
      setSourceId(selectedSource.id);
    }
    const shot = await withTimeout(window.eegDesktop.captureSource(selectedSource.id), 8000);
    return { sourceId: shot.sourceId, sourceName: selectedSource.name, dataUrl: shot.dataUrl };
  };

  const applyLatestReportToNotion = async () => {
    const report = session?.reports[0];
    if (!report) return;
    setNotionApplyPending(true);
    setNotionApplyMessage("Notionへ反映中...");
    try {
      const result = await api.applyReportToNotion(report.id);
      const commentText = result.commented_source_task ? " 元タスクへコメントも追加しました。" : "";
      setNotionApplyMessage(`反映しました。Todo ${result.created_tasks.length}件を作成しました。${commentText}`);
      await refresh();
    } catch (error) {
      setNotionApplyMessage(`Notionへの反映に失敗しました: ${String(error)}`);
    } finally {
      setNotionApplyPending(false);
    }
  };

  const chooseTask = (taskId: string) => {
    if (!taskId) {
      setNotionTaskId(undefined);
      setNotionProjectIds([]);
      return;
    }
    const task = taskOptions.find((item) => item.id === taskId);
    if (!task) return;
    setTodo(task.todo);
    setNotionTaskId(task.id);
    setNotionProjectIds(task.project_ids);
  };

  return (
    <main className={controlsLocked ? "app-booting" : ""} aria-busy={controlsLocked}>
      {!bootState.ready && <BootOverlay state={bootState} trace={bootTrace} heartbeat={heartbeat} />}
      <section className="topbar">
        <Metric color="cyan" label="フロー滞在" value={`${formatMinutes(countDuration(events, "フロー"))}`} />
        <Metric color="amber" label="手詰まり時間" value={`${formatMinutes(countDuration(events, "過負荷停止"))}`} />
        <Metric color="violet" label="停止イベント" value={`${events.filter((event) => event.label.includes("停止")).length}`} />
        <nav className="view-switch" aria-label="page navigation">
          <button className={view === "session" ? "active" : ""} onClick={() => setView("session")}>
            作業
          </button>
          <button className={view === "rag-graph" ? "active" : ""} onClick={() => setView("rag-graph")}>
            <Network size={16} />
            RAG Graph
          </button>
        </nav>
      </section>

      <section className="workspace">
        {view === "rag-graph" ? (
          <RagGraphView
            graph={ragGraph}
            loading={ragGraphLoading}
            error={ragGraphError}
            sessionId={graphSessionId}
            selectedNodeId={selectedGraphNodeId}
            rotation={graphRotation}
            onSelectNode={setSelectedGraphNodeId}
            onRotate={setGraphRotation}
            onRefresh={loadRagGraph}
          />
        ) : (
        <>
        <div className="left-column">
          <section className="control-panel">
            <div className="panel-title">
              <span>作業ブロック</span>
              <span>{stopping ? "停止処理中" : session?.active ? (autoRunning ? "自動記録中" : "記録中") : "停止中"}</span>
            </div>
            <div className="block-meta">
              <strong>{previewSessionId ? `履歴プレビュー ${previewSessionId}` : "同期タイムライン"}</strong>
              <span>{previewSessionId ? "recorded block" : "0-25分"}</span>
              <p>{formatSessionRange(session)}</p>
              <div className="block-status">
                <span>{formatOffset(timelineRange, scrubbedAt)}</span>
                {scrubEvent && <b className={`event-chip ${scrubEvent.severity}`}>{scrubEvent.label}</b>}
              </div>
              {scrubEvent?.reason && <p className="block-reason">{scrubEvent.reason}</p>}
              <div className="legend block-legend">
                <i className="flow" /> フロー <i className="normal" /> 通常 <i className="warning" /> 過負荷停止{" "}
                <i className="muted" /> 離脱停止
              </div>
            </div>
            <div className="block-control-stack">
              <div className="todo-row">
                <fieldset className="control-lock" disabled={controlsLocked}>
                  <button onClick={loadNotionTasks} disabled={session?.active || notionTasksLoading} title="Notionタスクを再読み込み">
                    <RefreshCw size={18} />
                  </button>
                  <select value={taskSelectValue} onChange={(event) => chooseTask(event.target.value)} disabled={session?.active}>
                    <option value="">Taskを選択</option>
                    {taskSelectStatus && <option value="__notion_status">{taskSelectStatus}</option>}
                    {taskOptions.map((task) => (
                      <option key={task.id} value={task.id}>
                        {task.todo}
                      </option>
                    ))}
                  </select>
                  {!notionTaskId && <input value={todo} onChange={(event) => setTodo(event.target.value)} />}
                </fieldset>
              </div>
              <div className="capture-controls compact">
                <fieldset className="control-lock" disabled={controlsLocked}>
                  <button
                    className={`screenshot-toggle ${screenshotEnabled ? "enabled" : "disabled"}`}
                    onClick={() => setScreenshotEnabled((value) => !value)}
                    title={screenshotEnabled ? "スクリーンショット記録を無効化" : "スクリーンショット記録を有効化"}
                  >
                    {screenshotEnabled ? <Camera size={18} /> : <CameraOff size={18} />}
                  </button>
                  <select value={sourceId} onChange={(event) => setSourceId(event.target.value)}>
                    <option value="">キャプチャ対象</option>
                    {sources.map((source) => (
                      <option key={source.id} value={source.id}>
                        {source.name}
                      </option>
                    ))}
                  </select>
                </fieldset>
              </div>
            </div>
            <div className="block-buttons">
              <fieldset className="control-lock block-action-lock" disabled={controlsLocked}>
                <button className={bleButtonClass} onClick={toggleBle} title={status?.ble.streaming ? "BLE切断" : "BLE接続"}>
                  <Radio size={22} />
                </button>
                <button className="record-button" onClick={session?.active ? stop : start} disabled={stopping} title={session?.active ? "停止" : "開始"}>
                  {session?.active ? <Square size={22} /> : <Play size={22} />}
                </button>
              </fieldset>
              <div className="block-ble-status">
                <Radio size={14} />
                <span>{status?.ble.detail ?? "API確認中"}</span>
              </div>
            </div>
            <div className="block-visualization">
              <div className="scrub-capture">
                {scrubCaptureUrl ? (
                  <>
                    <img src={scrubCaptureUrl} alt={scrubObservation?.source_name ?? "timeline capture"} />
                    <div className="scrub-capture-text">
                      <strong title={scrubObservation?.source_name ?? "capture"}>{cleanMojibake(scrubObservation?.source_name ?? "capture")}</strong>
                      <span>{scrubObservation ? formatTime(scrubObservation.captured_at) : ""}</span>
                      <p>{scrubSummary.description}</p>
                      {scrubSummary.ocr && <small>{scrubSummary.ocr}</small>}
                    </div>
                  </>
                ) : (
                  <p>この時刻に近いキャプチャはありません。</p>
                )}
              </div>
              <div className="graph-panel">
                <SignalChart
                  eeg={eeg}
                  activity={activity}
                  events={events}
                  range={timelineRange}
                  scrubRatio={scrubRatio}
                />
                <div className="graph-scrubber">
                  <Timeline
                    events={events}
                    range={timelineRange}
                    scrubRatio={scrubRatio}
                    onScrub={(value) => {
                      autoScrubRef.current = false;
                      setScrubRatio(value);
                    }}
                  />
                  <div className="timeline-scrubber">
                    <span>{formatOffset(timelineRange, scrubbedAt)}</span>
                  </div>
                </div>
              </div>
            </div>
          </section>
        </div>

        <aside className="details-panel">
          <div className="panel-title">
            <span>作業レポート + Todo 更新</span>
            {scrubEvent && <span className={`event-chip ${scrubEvent.severity}`}>{scrubEvent.label}</span>}
          </div>
          <div className="llm-card">
            <div className="card-title">
              <span />
              LLM フィードバック
            </div>
            <p className="report-body">
              {session?.reports[0]?.summary ??
                message ??
                "セッション終了時に、画面観測・EEG指標・操作ログ・RAGの類似記録を使って振り返りを生成します。"}
            </p>
            <div className="report-actions">
              {session?.reports[0]?.todo_suggestions?.map((suggestion) => (
                <div className="suggestion" key={suggestion}>
                  <CheckCircle2 size={16} />
                  {suggestion}
                </div>
              ))}
              {(session?.phases ?? []).map((phase) => (
                <div className="phase-note" key={phase.id}>
                  <strong>{phase.title}</strong>
                  <span>{phase.completed ? "完了候補" : "継続"}</span>
                  <p>{phase.summary}</p>
                </div>
              ))}
              {session?.reports[0] && (
                <>
                  <button className="wide-action" onClick={applyLatestReportToNotion} disabled={notionApplyPending}>
                    {notionApplyPending ? "Notionへ反映中..." : "Notionへ反映"}
                  </button>
                  {notionApplyMessage && (
                    <div className="sync-message" role="status" aria-live="polite">
                      <CheckCircle2 size={16} />
                      <span>{notionApplyMessage}</span>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </aside>
        </>
        )}
      </section>
    </main>
  );
}

function BootOverlay({
  state,
  trace,
  heartbeat,
}: {
  state: BootState;
  trace: BootTraceEntry[];
  heartbeat: { frames: number; lastGapMs: number };
}) {
  return (
    <div className={`boot-overlay ${state.failed ? "failed" : ""}`} role="status" aria-live="polite">
      <div className="boot-card">
        <div className="boot-head">
          <RefreshCw size={22} className={state.failed ? "" : "spin"} />
          <div>
            <strong>{state.label}</strong>
            <span>{state.detail}</span>
          </div>
        </div>
        <div className="boot-heartbeat">renderer heartbeat: frame {heartbeat.frames}, last gap {heartbeat.lastGapMs}ms</div>
        <ol className="boot-trace">
          {trace.map((entry, index) => (
            <li key={`${entry.at}-${entry.label}-${index}`}>
              <time>{entry.at}</time>
              <code>{entry.label}</code>
              {entry.detail && <span>{entry.detail}</span>}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function RagGraphView({
  graph,
  loading,
  error,
  sessionId,
  selectedNodeId,
  rotation,
  onSelectNode,
  onRotate,
  onRefresh,
}: {
  graph: RagGraphResponse | null;
  loading: boolean;
  error: string;
  sessionId: string;
  selectedNodeId: string | null;
  rotation: { x: number; y: number };
  onSelectNode: (nodeId: string) => void;
  onRotate: (rotation: { x: number; y: number }) => void;
  onRefresh: () => void;
}) {
  const dragRef = useRef<{ x: number; y: number; rotation: { x: number; y: number } } | null>(null);
  const nodes = graph?.nodes ?? [];
  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const projected = useMemo(() => projectGraphNodes(nodes, rotation), [nodes, rotation]);
  const projectedById = useMemo(() => new Map(projected.map((node) => [node.id, node])), [projected]);
  const selectedNode = (selectedNodeId && nodeById.get(selectedNodeId)) || nodes[0] || null;
  const focusUpEdges = graph?.edges.filter((edge) => edge.focus_up).length ?? 0;
  const workloadUpEdges = graph?.edges.filter((edge) => edge.workload_up).length ?? 0;
  const embeddedNodes = nodes.filter((node) => node.has_embedding).length;

  const startDrag = (event: React.PointerEvent<SVGSVGElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { x: event.clientX, y: event.clientY, rotation };
  };
  const drag = (event: React.PointerEvent<SVGSVGElement>) => {
    const start = dragRef.current;
    if (!start) return;
    onRotate({
      x: start.rotation.x + (event.clientY - start.y) / 260,
      y: start.rotation.y + (event.clientX - start.x) / 260,
    });
  };
  const stopDrag = () => {
    dragRef.current = null;
  };

  return (
    <section className="rag-page">
      <div className="rag-toolbar">
        <div>
          <strong>RAG Graph</strong>
          <span>{sessionId}</span>
        </div>
        <button onClick={onRefresh} disabled={loading}>
          <RefreshCw size={16} />
          {loading ? "Loading" : "更新"}
        </button>
      </div>
      <div className="rag-stage">
        <svg
          className="rag-canvas"
          viewBox="0 0 960 620"
          role="img"
          aria-label="Chroma embedding graph"
          onPointerDown={startDrag}
          onPointerMove={drag}
          onPointerUp={stopDrag}
          onPointerCancel={stopDrag}
        >
          <defs>
            <marker id="arrow-gray" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">
              <path d="M 0 0 L 9 4.5 L 0 9 z" />
            </marker>
            <marker id="arrow-blue" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">
              <path d="M 0 0 L 9 4.5 L 0 9 z" />
            </marker>
            <marker id="arrow-orange" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">
              <path d="M 0 0 L 9 4.5 L 0 9 z" />
            </marker>
          </defs>
          <line className="axis x" x1="120" y1="520" x2="840" y2="520" />
          <line className="axis y" x1="120" y1="520" x2="120" y2="100" />
          <line className="axis z" x1="120" y1="520" x2="250" y2="390" />
          {(graph?.edges ?? []).map((edge) => {
            const source = projectedById.get(edge.source);
            const target = projectedById.get(edge.target);
            if (!source || !target) return null;
            return (
              <line
                key={edge.id}
                className={`rag-edge ${edge.focus_up ? "focus-up" : ""} ${edge.workload_up ? "workload-up" : ""}`}
                x1={source.screenX}
                y1={source.screenY}
                x2={target.screenX}
                y2={target.screenY}
                markerEnd={edge.workload_up ? "url(#arrow-orange)" : edge.focus_up ? "url(#arrow-blue)" : "url(#arrow-gray)"}
              />
            );
          })}
          {[...projected]
            .sort((a, b) => a.depth - b.depth)
            .map((node) => (
              <circle
                key={node.id}
                className={`rag-node ${node.id === selectedNode?.id ? "selected" : ""}`}
                cx={node.screenX}
                cy={node.screenY}
                r={7 * node.scale}
                onClick={() => onSelectNode(node.id)}
              >
                <title>{`${cleanMojibake(node.label)} ${formatTime(node.started_at)}`}</title>
              </circle>
            ))}
        </svg>
        {loading && <div className="rag-overlay">Chroma embedding を読み込み中</div>}
        {!loading && error && <div className="rag-overlay error">{error}</div>}
        {!loading && !error && !nodes.length && <div className="rag-overlay">表示できるエピソードがありません。</div>}
      </div>
      <aside className="rag-inspector">
        <div className="rag-stats">
          <Metric color="cyan" label="ノード" value={`${graph?.node_count ?? 0}`} />
          <Metric color="blue" label="集中上昇エッジ" value={`${focusUpEdges}`} />
          <Metric color="amber" label="認知負荷上昇エッジ" value={`${workloadUpEdges}`} />
          <Metric color="violet" label="埋め込み取得" value={`${embeddedNodes}`} />
        </div>
        <div className="rag-detail">
          <strong>{selectedNode ? `${formatTime(selectedNode.started_at)} ${cleanMojibake(selectedNode.label)}` : "選択なし"}</strong>
          <span>
            backend: {graph?.embedding_backend ?? "unknown"} / edges: {graph?.edge_count ?? 0}
          </span>
          {selectedNode && (
            <>
              <p>{selectedNode.summary}</p>
              <small>
                engagement {formatNullableNumber(selectedNode.engagement)} / workload {formatNullableNumber(selectedNode.workload)}
              </small>
              <small>{cleanMojibake(selectedNode.active_window)}</small>
            </>
          )}
        </div>
      </aside>
    </section>
  );
}

function Metric({ color, label, value }: { color: string; label: string; value: string }) {
  return (
    <div className="metric">
      <i className={color} />
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Timeline({
  events,
  range,
  scrubRatio,
  onScrub,
}: {
  events: EventRecord[];
  range: TimeRange;
  scrubRatio: number;
  onScrub: (value: number) => void;
}) {
  const bands = timelineBands(events);
  return (
    <div className="timeline">
      <div className="timeline-base">
        {bands.map((band, index) => {
          const start = band.startedAt;
          const end = band.endedAt;
          const left = ratioInRange(start, range) * 100;
          const width = Math.max(0.4, (ratioInRange(end, range) - ratioInRange(start, range)) * 100);
          return (
            <span
              key={`${band.label}-${band.startedAt}-${index}`}
              className={`segment ${band.className}`}
              style={{ left: `${left}%`, width: `${width}%` }}
              title={`${band.label} ${band.count}件`}
            />
          );
        })}
        <input
          className="timeline-range"
          type="range"
          min="0"
          max="1000"
          value={Math.round(scrubRatio * 1000)}
          onChange={(event) => onScrub(Number(event.target.value) / 1000)}
          aria-label="timeline scrub position"
        />
        <span className="cursor" style={{ left: `${scrubRatio * 100}%` }} />
      </div>
    </div>
  );
}

function SignalChart({
  eeg,
  activity,
  range,
  scrubRatio,
}: {
  eeg: EegWindowRecord[];
  activity: ActivityWindowRecord[];
  events: EventRecord[];
  range: TimeRange;
  scrubRatio: number;
}) {
  const hasApproachAvoidance = eeg.some((item) => item.approach_avoidance_available);
  const activityData = activity;
  const hasActivity = activityData.length > 0;
  const data = eeg.length
    ? eeg
    : Array.from({ length: 24 }, (_, index) => ({
        started_at: toTokyoIsoString(new Date(Date.now() - (23 - index) * 30_000)),
        engagement: 0.45 + Math.sin(index / 4) * 0.12,
        workload: 0.85 + Math.cos(index / 5) * 0.2,
        approach_avoidance: Math.sin(index / 3) * 0.25,
        approach_avoidance_available: false,
      }));
  const eegSeries = {
    workload: toSeries(data, "workload"),
    engagement: toSeries(data, "engagement"),
    approach_avoidance: toSeries(data, "approach_avoidance"),
  };
  const activitySeries = {
    key_count: toSeries(activityData, "key_count"),
    mouse_distance: toSeries(activityData, "mouse_distance"),
    click_count: toSeries(activityData, "click_count"),
    scroll_count: toSeries(activityData, "scroll_count"),
  };
  const points = (series: ChartSeries) => normalizedPath(series, range);
  const cursorX = chartX(range.min + scrubRatio * Math.max(1, range.max - range.min), range);
  return (
    <div className="chart-block">
      <div className="chart-legend" aria-label="EEG指標と操作量の凡例">
        <span><i className="workload" />認知負荷 theta/alpha</span>
        <span><i className="engagement" />課題関与 beta/(alpha+theta)</span>
        {hasApproachAvoidance && <span><i className="approach-avoidance" />接近/回避 FAA</span>}
        {hasActivity && <span><i className="keyboard-activity" />キーボード</span>}
        {hasActivity && <span><i className="mouse-activity" />マウス移動</span>}
        {hasActivity && <span><i className="click-activity" />クリック</span>}
        {hasActivity && <span><i className="scroll-activity" />スクロール</span>}
      </div>
      <svg className="chart" viewBox="0 0 930 250" preserveAspectRatio="none" role="img" aria-label="EEG and input activity trends">
        <line className="chart-grid-major" x1={CHART_LEFT} x2={CHART_RIGHT} y1={chartYForZScore(2)} y2={chartYForZScore(2)} />
        <line className="chart-grid-minor" x1={CHART_LEFT} x2={CHART_RIGHT} y1={chartYForZScore(1)} y2={chartYForZScore(1)} />
        <line className="chart-grid-major" x1={CHART_LEFT} x2={CHART_RIGHT} y1={chartYForZScore(0)} y2={chartYForZScore(0)} />
        <line className="chart-grid-minor" x1={CHART_LEFT} x2={CHART_RIGHT} y1={chartYForZScore(-1)} y2={chartYForZScore(-1)} />
        <line className="chart-grid-major" x1={CHART_LEFT} x2={CHART_RIGHT} y1={chartYForZScore(-2)} y2={chartYForZScore(-2)} />
        <path className="line workload" d={points(eegSeries.workload)} />
        <path className="line engagement" d={points(eegSeries.engagement)} />
        {hasApproachAvoidance && (
          <path
            className="line approach-avoidance"
            d={points(eegSeries.approach_avoidance)}
          />
        )}
        {hasActivity && <path className="line keyboard-activity" d={points(activitySeries.key_count)} />}
        {hasActivity && (
          <path
            className="line mouse-activity"
            d={points(activitySeries.mouse_distance)}
          />
        )}
        {hasActivity && <path className="line click-activity" d={points(activitySeries.click_count)} />}
        {hasActivity && <path className="line scroll-activity" d={points(activitySeries.scroll_count)} />}
        <line className="chart-cursor" x1={cursorX} x2={cursorX} y1="48" y2="226" />
        <text x="28" y="52">高</text>
        <text x="28" y="136">中</text>
        <text x="28" y="218">低</text>
      </svg>
    </div>
  );
}

type ChartSeries = Array<{ at: number; value: number }>;
type TimeRange = { min: number; max: number };
type ProjectedGraphNode = RagGraphNode & {
  screenX: number;
  screenY: number;
  depth: number;
  scale: number;
};

const SESSION_DURATION_MS = 25 * 60 * 1000;
const CHART_LEFT = 30;
const CHART_RIGHT = 900;
const CHART_BOTTOM = 220;
const CHART_HEIGHT = 160;
const NORMALIZATION_SIGMA_LIMIT = 2;

function projectGraphNodes(nodes: RagGraphNode[], rotation: { x: number; y: number }): ProjectedGraphNode[] {
  const cosX = Math.cos(rotation.x);
  const sinX = Math.sin(rotation.x);
  const cosY = Math.cos(rotation.y);
  const sinY = Math.sin(rotation.y);
  return nodes.map((node) => {
    const y1 = node.y * cosX - node.z * sinX;
    const z1 = node.y * sinX + node.z * cosX;
    const x2 = node.x * cosY + z1 * sinY;
    const z2 = -node.x * sinY + z1 * cosY;
    const perspective = 1 / (1.9 - z2 * 0.45);
    return {
      ...node,
      screenX: 480 + x2 * 310 * perspective,
      screenY: 310 + y1 * 260 * perspective,
      depth: z2,
      scale: Math.max(0.72, Math.min(1.32, perspective * 1.15)),
    };
  });
}

function toSeries<T extends { started_at: string }>(items: T[], key: keyof T): ChartSeries {
  return items
    .map((item) => ({
      at: Date.parse(item.started_at),
      value: Number(item[key]),
    }))
    .filter((point) => Number.isFinite(point.at) && Number.isFinite(point.value));
}

function normalizedPath(
  series: ChartSeries,
  range: TimeRange,
) {
  if (!series.length) return "";
  const blockStats = statsFor(series);
  const points = series
    .map((point) => {
      const normalized = normalizeToPercent(point.value, blockStats);
      return { x: chartX(point.at, range), y: chartYForPercent(normalized) };
    });
  return curvePath(points);
}

function chartX(timestamp: number, range: TimeRange) {
  return CHART_LEFT + ratioInRange(timestamp, range) * (CHART_RIGHT - CHART_LEFT);
}

function chartYForPercent(percent: number) {
  return CHART_BOTTOM - percent * (CHART_HEIGHT / 100);
}

function chartYForZScore(zScore: number) {
  return chartYForPercent(normalizedPercentForZScore(zScore));
}

function ratioInRange(timestamp: number, range: TimeRange) {
  return Math.min(1, Math.max(0, (timestamp - range.min) / Math.max(1, range.max - range.min)));
}

function curvePath(points: Array<{ x: number; y: number }>) {
  if (!points.length) return "";
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;
  const commands = [`M ${points[0].x} ${points[0].y}`];
  for (let index = 0; index < points.length - 1; index += 1) {
    const previous = points[Math.max(0, index - 1)];
    const current = points[index];
    const next = points[index + 1];
    const afterNext = points[Math.min(points.length - 1, index + 2)];
    const cp1x = current.x + (next.x - previous.x) / 6;
    const cp1y = current.y + (next.y - previous.y) / 6;
    const cp2x = next.x - (afterNext.x - current.x) / 6;
    const cp2y = next.y - (afterNext.y - current.y) / 6;
    commands.push(`C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${next.x} ${next.y}`);
  }
  return commands.join(" ");
}

function statsFor(series: ChartSeries): NormalizationMetricStats | undefined {
  if (!series.length) return undefined;
  const mean = series.reduce((sum, point) => sum + point.value, 0) / series.length;
  const variance = series.reduce((sum, point) => sum + (point.value - mean) ** 2, 0) / series.length;
  return { mean, variance, count: series.length };
}

function normalizeToPercent(value: number, stats?: NormalizationMetricStats) {
  if (!stats || stats.count < 1) return 50;
  const std = Math.sqrt(Math.max(0, stats.variance));
  if (std < Number.EPSILON) return 50;
  const zScore = (value - stats.mean) / std;
  return normalizedPercentForZScore(zScore);
}

function normalizedPercentForZScore(zScore: number) {
  const clipped = Math.min(NORMALIZATION_SIGMA_LIMIT, Math.max(-NORMALIZATION_SIGMA_LIMIT, zScore));
  return ((clipped + NORMALIZATION_SIGMA_LIMIT) / (NORMALIZATION_SIGMA_LIMIT * 2)) * 100;
}

function sessionTimeRange(
  session: SessionResponse | null,
  eeg: EegWindowRecord[],
  activity: ActivityWindowRecord[],
): TimeRange {
  const sessionStart = session?.session?.started_at ? Date.parse(session.session.started_at) : NaN;
  if (Number.isFinite(sessionStart)) {
    const sessionEnd = session?.session?.ended_at ? Date.parse(session.session.ended_at) : sessionStart + SESSION_DURATION_MS;
    return { min: sessionStart, max: Math.max(sessionStart + 1, sessionEnd) };
  }
  const timestamps = [
    ...eeg.map((item) => Date.parse(item.started_at)),
    ...activity.map((item) => Date.parse(item.started_at)),
  ].filter(Number.isFinite);
  const min = Math.min(...timestamps);
  if (!Number.isFinite(min)) return { min: 0, max: SESSION_DURATION_MS };
  return { min, max: min + SESSION_DURATION_MS };
}

function nearestObservation(observations: ObservationRecord[], timestamp: number) {
  if (!observations.length || !Number.isFinite(timestamp)) return null;
  return observations.reduce((best, observation) => {
    const bestDistance = Math.abs(Date.parse(best.captured_at) - timestamp);
    const distance = Math.abs(Date.parse(observation.captured_at) - timestamp);
    return distance < bestDistance ? observation : best;
  }, observations[0]);
}

function eventAt(events: EventRecord[], timestamp: number) {
  if (!events.length || !Number.isFinite(timestamp)) return null;
  return events.find((event) => {
    const startedAt = Date.parse(event.started_at);
    const endedAt = Date.parse(event.ended_at);
    return Number.isFinite(startedAt) && Number.isFinite(endedAt) && startedAt <= timestamp && timestamp <= endedAt;
  }) ?? nearestEvent(events, timestamp);
}

function nearestEvent(events: EventRecord[], timestamp: number) {
  return events.reduce((best, event) => {
    const bestDistance = Math.abs(Date.parse(best.started_at) - timestamp);
    const distance = Math.abs(Date.parse(event.started_at) - timestamp);
    return distance < bestDistance ? event : best;
  }, events[0]);
}

function captureUrl(sessionId: string | undefined, observation: ObservationRecord | null) {
  if (!sessionId || !observation?.image_path) return "";
  const filename = observation.image_path.split(/[\\/]/).pop();
  return filename ? api.assetUrl(`/api/captures/${encodeURIComponent(sessionId)}/${encodeURIComponent(filename)}`) : "";
}

function observationSummary(observation: ObservationRecord | null) {
  if (!observation) return { description: "", ocr: "" };
  const raw = observation.description.trim();
  const parsed = parseObservationJson(raw);
  return {
    description: cleanMojibake(parsed.description || raw),
    ocr: cleanMojibake(parsed.ocr_text || observation.ocr_text || ""),
  };
}

function parseObservationJson(value: string): { description?: string; ocr_text?: string } {
  const jsonText = value.replace(/^```json\s*/i, "").replace(/```$/i, "").trim();
  try {
    const parsed = JSON.parse(jsonText) as { description?: unknown; ocr_text?: unknown };
    return {
      description: typeof parsed.description === "string" ? parsed.description : undefined,
      ocr_text: typeof parsed.ocr_text === "string" ? parsed.ocr_text : undefined,
    };
  } catch {
    return {};
  }
}

function cleanMojibake(value: string) {
  return value.replace(/[�]+/g, "").replace(/\s+/g, " ").trim();
}

function formatOffset(range: TimeRange, timestamp: number) {
  const seconds = Math.max(0, Math.round((timestamp - range.min) / 1000));
  const minutes = Math.floor(seconds / 60);
  return `${minutes.toString().padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`;
}

function liveScrubTimestamp(
  range: TimeRange,
  eeg: EegWindowRecord[],
  activity: ActivityWindowRecord[],
  events: EventRecord[],
  observations: ObservationRecord[],
) {
  const latestDataAt = Math.max(
    range.min,
    ...eeg.map((item) => Date.parse(item.started_at)).filter(Number.isFinite),
    ...activity.map((item) => Date.parse(item.started_at)).filter(Number.isFinite),
    ...events.map((item) => Date.parse(item.started_at)).filter(Number.isFinite),
  );
  const completeObservations = observations
    .filter((observation) => observation.image_path && observation.description.trim())
    .map((observation) => ({ observation, at: Date.parse(observation.captured_at) }))
    .filter((item) => Number.isFinite(item.at))
    .sort((a, b) => a.at - b.at);
  if (!completeObservations.length) return latestDataAt;
  const latest = completeObservations[completeObservations.length - 1];
  const previous = completeObservations[completeObservations.length - 2];
  if (latest.at >= latestDataAt - 5_000) return latest.at;
  return previous?.at ?? latest.at;
}

function countDuration(events: EventRecord[], label: string) {
  const minutes = events
    .filter((event) => event.label === label)
    .reduce((sum, event) => sum + Math.max(0, Date.parse(event.ended_at) - Date.parse(event.started_at)) / 60_000, 0);
  return minutes;
}

function formatMinutes(value: number) {
  return `${Math.floor(value).toString().padStart(2, "0")}:${Math.round((value % 1) * 60)
    .toString()
    .padStart(2, "0")}`;
}

function formatSessionRange(session: SessionResponse | null) {
  const startedAt = session?.session?.started_at;
  if (!startedAt) return "未開始";
  const endedAt = session?.session?.ended_at;
  return `${formatTime(startedAt)} 開始 / ${endedAt ? `${formatTime(endedAt)} 終了` : "進行中"}`;
}

function toTokyoIsoString(date = new Date()) {
  const tokyoDate = new Date(date.getTime() + 9 * 60 * 60 * 1000);
  const datePart = [
    tokyoDate.getUTCFullYear(),
    padDatePart(tokyoDate.getUTCMonth() + 1),
    padDatePart(tokyoDate.getUTCDate()),
  ].join("-");
  const timePart = [
    padDatePart(tokyoDate.getUTCHours()),
    padDatePart(tokyoDate.getUTCMinutes()),
    padDatePart(tokyoDate.getUTCSeconds()),
  ].join(":");
  const milliseconds = tokyoDate.getUTCMilliseconds().toString().padStart(3, "0");
  return `${datePart}T${timePart}.${milliseconds}+09:00`;
}

function padDatePart(value: number) {
  return value.toString().padStart(2, "0");
}

function formatTime(value: string) {
  return new Date(value).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" });
}

function formatNullableNumber(value: number | null) {
  return value === null ? "n/a" : value.toFixed(3);
}

function eventClass(event: EventRecord) {
  if (event.label === "フロー") return "flow";
  if (event.label === "過負荷停止") return "warning";
  if (event.label === "逸脱停止") return "muted";
  return "normal";
}

function timelineBands(events: EventRecord[]) {
  const sorted = [...events]
    .map((event) => ({
      label: event.label,
      className: eventClass(event),
      startedAt: Date.parse(event.started_at),
      endedAt: Date.parse(event.ended_at),
      count: 1,
    }))
    .filter((event) => Number.isFinite(event.startedAt) && Number.isFinite(event.endedAt))
    .sort((a, b) => a.startedAt - b.startedAt);
  const bands: Array<(typeof sorted)[number]> = [];
  for (const event of sorted) {
    const previous = bands[bands.length - 1];
    if (previous && previous.className === event.className && event.startedAt - previous.endedAt <= 5_000) {
      previous.endedAt = Math.max(previous.endedAt, event.endedAt);
      previous.count += event.count;
      continue;
    }
    bands.push({ ...event });
  }
  return bands;
}


function withTimeout<T>(promise: Promise<T>, timeoutMs: number, fallback?: T): Promise<T> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = window.setTimeout(() => {
      if (settled) return;
      settled = true;
      if (fallback !== undefined) {
        resolve(fallback);
        return;
      }
      reject(new Error(`timeout after ${timeoutMs}ms`));
    }, timeoutMs);
    promise
      .then((value) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        resolve(value);
      })
      .catch((error) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        reject(error);
      });
  });
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

const rootElement = document.getElementById("root");
if (!rootElement) {
  document.body.innerHTML = "<main style='padding:32px;color:#d8dee9'>React root element was not found.</main>";
} else {
  window.addEventListener("error", (event) => {
    rootElement.innerHTML = `<main style="padding:32px;color:#d8dee9;font-family:Segoe UI,sans-serif">
      <h1>Renderer error</h1>
      <pre style="white-space:pre-wrap;background:#111923;padding:16px;border-radius:8px">${escapeText(
        event.message,
      )}</pre>
    </main>`;
  });
  window.addEventListener("unhandledrejection", (event) => {
    rootElement.innerHTML = `<main style="padding:32px;color:#d8dee9;font-family:Segoe UI,sans-serif">
      <h1>Unhandled renderer rejection</h1>
      <pre style="white-space:pre-wrap;background:#111923;padding:16px;border-radius:8px">${escapeText(
        String(event.reason),
      )}</pre>
    </main>`;
  });
  createRoot(rootElement).render(<App />);
}

function escapeText(value: string) {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
