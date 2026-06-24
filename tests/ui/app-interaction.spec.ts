import { expect, test, type Page } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.eegDesktop = {
      backendUrl: "http://127.0.0.1:8766",
      runtimeToken: "",
    } as typeof window.eegDesktop;
  });
  await mockApi(page);
});

test("main controls accept pointer, keyboard, and scroll interactions", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("main")).toBeVisible();
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);

  const buttons = page.locator(".todo-row button");
  await expect(buttons.first()).toBeVisible();
  await expect(buttons.first()).toBeEnabled();

  const firstButton = page.getByTitle("開始");
  await firstButton.click({ trial: true });

  await firstButton.hover();
  await expect.poll(() => firstButton.evaluate((element) => element.matches(":hover"))).toBe(true);

  const taskSelect = page.locator(".todo-row select").first();
  await taskSelect.selectOption("");
  const todoInput = page.locator(".todo-row input");
  await todoInput.click();
  await expect(todoInput).toBeFocused();
  await todoInput.fill("UI smoke task");
  await expect(todoInput).toHaveValue("UI smoke task");

  await page.mouse.wheel(0, 700);
  await expect(page.locator("main")).toBeVisible();
});

test("notion task selector is populated and selectable", async ({ page }) => {
  await page.goto("/");

  const taskSelect = page.locator(".todo-row select").first();
  await expect(taskSelect).toBeEnabled();
  await expect.poll(() => taskSelect.locator("option").count()).toBeGreaterThan(1);

  await taskSelect.selectOption("task-2");
  await expect(page.locator(".todo-row input")).toHaveCount(0);
  await expect(taskSelect).toHaveValue("task-2");
});

test("rag graph page renders embedding nodes and focus edges", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("button", { name: /RAG Graph/ }).click();

  await expect(page.locator(".rag-page")).toBeVisible();
  await expect(page.locator(".rag-node")).toHaveCount(2);
  await expect(page.locator(".rag-edge.focus-up")).toHaveCount(1);
  await expect(page.locator(".rag-edge.workload-up")).toHaveCount(1);
  await expect(page.locator(".rag-detail")).toContainText("mock episode");
});

async function mockApi(page: Page) {
  const status = {
    ok: true,
    session_active: false,
    session_id: null,
    ble: {
      state: "disconnected",
      detail: "BLE未接続",
      streaming: false,
      receiving: false,
      sample_count: 0,
      features: { ready: false, data: null, signal_quality: [] },
    },
    gemini: {
      available: false,
      model: "gemini-test",
      embedding_model: "embedding-test",
    },
    capture: {
      directory: "captures",
      interval_seconds: 30,
      active_session_capture_count: 0,
    },
    database: {
      path: "data/app.sqlite3",
      ready: true,
    },
    rag: {
      backend: "sqlite-fallback",
    },
    notion: {
      configured: true,
      tasks_data_source_id: true,
      projects_data_source_id: false,
      notion_version: "2025-09-03",
      last_error: "",
    },
  };

  const session = {
    active: false,
    session: null,
    eeg_windows: [],
    activity_windows: [],
    normalization_baseline: { source_session_id: null, eeg: {}, activity: {} },
    observations: [],
    events: [],
    episodes: [],
    phases: [],
    reports: [],
  };

  const notionTasks = {
    configured: true,
    error: "",
    status: status.notion,
    tasks: [
      {
        id: "task-1",
        title: "First task",
        status: "In Progress",
        project_ids: [],
        project_names: [],
        due: null,
        url: "https://example.test/task-1",
        todo: "First task / In Progress",
      },
      {
        id: "task-2",
        title: "Second task",
        status: "Not Started",
        project_ids: [],
        project_names: [],
        due: null,
        url: "https://example.test/task-2",
        todo: "Second task / Not Started",
      },
    ],
  };

  const ragGraph = {
    session_id: "20260623-132529-ebe052",
    embedding_backend: "chroma",
    node_count: 2,
    edge_count: 1,
    nodes: [
      {
        id: "chunk-1",
        episode_id: 1,
        session_id: "20260623-132529-ebe052",
        started_at: "2026-06-23T13:25:31.122Z",
        ended_at: "2026-06-23T13:26:01.122Z",
        label: "通常",
        severity: "normal",
        active_window: "PowerPoint",
        summary: "mock episode one",
        engagement: 0.2,
        workload: 0.4,
        x: -0.4,
        y: 0.1,
        z: 0.2,
        has_embedding: true,
      },
      {
        id: "chunk-2",
        episode_id: 2,
        session_id: "20260623-132529-ebe052",
        started_at: "2026-06-23T13:26:01.122Z",
        ended_at: "2026-06-23T13:26:31.122Z",
        label: "フロー",
        severity: "good",
        active_window: "PowerPoint",
        summary: "mock episode two",
        engagement: 0.5,
        workload: 0.3,
        x: 0.4,
        y: -0.1,
        z: -0.2,
        has_embedding: true,
      },
    ],
    edges: [
      {
        id: "chunk-1->chunk-2",
        source: "chunk-1",
        target: "chunk-2",
        session_id: "20260623-132529-ebe052",
        focus_delta: 0.3,
        focus_up: true,
        workload_delta: 0.2,
        workload_up: true,
      },
    ],
  };

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/health") return route.fulfill({ json: { ok: true } });
    if (path === "/api/runtime") {
      return route.fulfill({
        json: {
          ok: true,
          protocol: "eeg-task-scheduler-runtime-v2",
          pid: 1234,
          project_root: "test",
          backend_url: "http://127.0.0.1:8766",
          runtime_token: "",
        },
      });
    }
    if (path === "/api/status") return route.fulfill({ json: status });
    if (path === "/api/session/current") return route.fulfill({ json: session });
    if (path === "/api/rag/graph") return route.fulfill({ json: ragGraph });
    if (path === "/api/todos/initial") {
      return route.fulfill({
        json: {
          todo: "First task / In Progress",
          source: "notion",
          task: { id: "task-1", project_ids: [], project_names: [] },
        },
      });
    }
    if (path === "/api/todos/notion") return route.fulfill({ json: notionTasks });
    return route.fulfill({ status: 404, json: { detail: `Unhandled mock route: ${path}` } });
  });
}
