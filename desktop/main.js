const { app, BrowserWindow, desktopCapturer, ipcMain, screen } = require("electron");
const { execFile, spawn } = require("child_process");
const crypto = require("crypto");
const net = require("net");
const path = require("path");

const projectRoot = path.resolve(__dirname, "..");
let backendUrl = process.env.EEG_BACKEND_URL || "";
let backendPort = 8766;
let runtimeToken = process.env.EEG_RUNTIME_TOKEN || "";
const runtimeProtocol = "eeg-task-scheduler-runtime-v2";
let backendProcess = null;
let mainWindow = null;
let inputMonitorProcess = null;
let globalInputTotals = emptyInputSnapshot();
let lastInputReadTotals = emptyInputSnapshot();

app.commandLine.appendSwitch("remote-debugging-port", "9223");

async function findAvailablePort(startPort = 8766, endPort = 8780) {
  for (let port = startPort; port <= endPort; port += 1) {
    if (await canListen(port)) return port;
  }
  throw new Error(`No free backend port found in ${startPort}-${endPort}`);
}

function canListen(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => {
      server.close(() => resolve(true));
    });
    server.listen(port, "127.0.0.1");
  });
}

async function startBackend() {
  if (process.env.EEG_BACKEND_URL) return;
  backendPort = await findAvailablePort();
  backendUrl = `http://127.0.0.1:${backendPort}`;
  runtimeToken = runtimeToken || crypto.randomUUID().replaceAll("-", "");
  backendProcess = spawn("uv", ["run", "eeg-task-scheduler", "--host", "127.0.0.1", "--port", String(backendPort)], {
    cwd: projectRoot,
    shell: true,
    windowsHide: true,
    env: { ...process.env, ELECTRON_RUN_AS_NODE: "", EEG_RUNTIME_TOKEN: runtimeToken },
  });
  backendProcess.stderr.on("data", (data) => console.error(`[backend] ${data}`));
}

async function waitForUrl(url, deadlineMs = 25000, requestTimeoutMs = 1500) {
  const started = Date.now();
  while (Date.now() - started < deadlineMs) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), requestTimeoutMs);
    try {
      const response = await fetch(url, { signal: controller.signal });
      if (response.ok) return true;
    } catch (_) {
      // Keep polling until the overall deadline. The renderer should not stay blank
      // just because a readiness probe hangs.
    } finally {
      clearTimeout(timeout);
    }
    if (Date.now() - started < deadlineMs) {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  return false;
}

async function waitForBackend(deadlineMs = 25000) {
  const started = Date.now();
  while (Date.now() - started < deadlineMs) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 1500);
    try {
      const response = await fetch(`${backendUrl}/api/runtime`, { signal: controller.signal });
      if (response.ok) {
        const runtime = await response.json();
        const tokenMatches = !runtimeToken || runtime.runtime_token === runtimeToken;
        if (runtime.protocol === runtimeProtocol && tokenMatches) return true;
        console.error(`[desktop] backend runtime mismatch: ${JSON.stringify(runtime)}`);
      }
    } catch (_) {
      // Keep polling until the deadline. A stale backend without /api/runtime is not ready.
    } finally {
      clearTimeout(timeout);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1500,
    height: 980,
    minWidth: 1060,
    minHeight: 760,
    backgroundColor: "#0d1218",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--backend-url=${backendUrl}`, `--runtime-token=${runtimeToken}`],
    },
  });

  mainWindow.webContents.on("render-process-gone", (_event, details) => {
    console.error(`[renderer] process gone: ${JSON.stringify(details)}`);
  });
  mainWindow.webContents.setZoomFactor(1);

  const devUrl = process.env.EEG_WEB_DEV_URL;
  let backendReadyForDiagnostics = false;
  if (devUrl) {
    const [ready, webReady] = await Promise.all([waitForBackend(60000), waitForUrl(devUrl, 60000)]);
    backendReadyForDiagnostics = ready;
    if (ready && webReady) {
      await mainWindow.loadURL(devUrl);
    } else {
      await mainWindow.loadURL(diagnosticHtml("Development servers did not start", { backendReady: ready, webReady, devUrl, backendUrl }));
    }
  } else {
    const ready = await waitForBackend();
    backendReadyForDiagnostics = ready;
    if (ready) {
      await mainWindow.loadURL(backendUrl);
    } else {
      await mainWindow.loadURL(diagnosticHtml("Backend did not start", { backendReady: ready, devUrl, backendUrl }));
    }
  }

  mainWindow.webContents.on("did-fail-load", async (_event, _code, description, validatedUrl) => {
    if (validatedUrl === devUrl && await waitForUrl(devUrl, 10000)) {
      await mainWindow.loadURL(devUrl);
      return;
    }
    await mainWindow.loadURL(
      diagnosticHtml(`Renderer load failed: ${description}`, { backendReady: backendReadyForDiagnostics, devUrl, backendUrl }),
    );
  });
}

function diagnosticHtml(title, details) {
  const body = `
    <html>
      <head>
        <meta charset="utf-8" />
        <style>
          body { margin: 0; background: #0d1218; color: #d8dee9; font-family: Segoe UI, sans-serif; }
          main { padding: 32px; line-height: 1.7; }
          code { background: #182232; padding: 2px 6px; border-radius: 4px; }
          pre { background: #111923; padding: 16px; border-radius: 8px; overflow: auto; }
        </style>
      </head>
      <body>
        <main>
          <h1>${escapeHtml(title)}</h1>
          <p>開発起動では backend と Vite の両方が起動してから UI を読み込みます。</p>
          <pre>${escapeHtml(JSON.stringify(details, null, 2))}</pre>
          <p>ターミナルに <code>[1] VITE</code> の起動ログが出ているか確認してください。</p>
        </main>
      </body>
    </html>
  `;
  return `data:text/html;charset=utf-8,${encodeURIComponent(body)}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function emptyInputSnapshot() {
  return {
    key_count: 0,
    mouse_distance: 0,
    click_count: 0,
    scroll_count: 0,
  };
}

function subtractInputSnapshot(current, previous) {
  return {
    key_count: Math.max(0, current.key_count - previous.key_count),
    mouse_distance: Math.max(0, current.mouse_distance - previous.mouse_distance),
    click_count: Math.max(0, current.click_count - previous.click_count),
    scroll_count: Math.max(0, current.scroll_count - previous.scroll_count),
  };
}

function startGlobalInputMonitor() {
  if (process.platform !== "win32" || inputMonitorProcess) return;
  const script = `
$ErrorActionPreference = "Stop"
Add-Type @"
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public class InputMonitor {
  private delegate IntPtr HookProc(int nCode, IntPtr wParam, IntPtr lParam);
  private static HookProc keyboardProc = KeyboardCallback;
  private static HookProc mouseProc = MouseCallback;
  private static IntPtr keyboardHook = IntPtr.Zero;
  private static IntPtr mouseHook = IntPtr.Zero;
  private static int keyCount = 0;
  private static int clickCount = 0;
  private static int scrollCount = 0;
  private static double mouseDistance = 0;
  private static int lastX = Int32.MinValue;
  private static int lastY = Int32.MinValue;
  private static readonly object gate = new object();

  private const int WH_KEYBOARD_LL = 13;
  private const int WH_MOUSE_LL = 14;
  private const int WM_KEYDOWN = 0x0100;
  private const int WM_SYSKEYDOWN = 0x0104;
  private const int WM_MOUSEMOVE = 0x0200;
  private const int WM_LBUTTONDOWN = 0x0201;
  private const int WM_RBUTTONDOWN = 0x0204;
  private const int WM_MBUTTONDOWN = 0x0207;
  private const int WM_MOUSEWHEEL = 0x020A;
  private const int WM_XBUTTONDOWN = 0x020B;
  private const int WM_MOUSEHWHEEL = 0x020E;

  [StructLayout(LayoutKind.Sequential)]
  private struct POINT { public int x; public int y; }

  [StructLayout(LayoutKind.Sequential)]
  private struct MSLLHOOKSTRUCT {
    public POINT pt;
    public uint mouseData;
    public uint flags;
    public uint time;
    public IntPtr dwExtraInfo;
  }

  [DllImport("user32.dll", SetLastError = true)]
  private static extern IntPtr SetWindowsHookEx(int idHook, HookProc lpfn, IntPtr hMod, uint dwThreadId);

  [DllImport("user32.dll", SetLastError = true)]
  [return: MarshalAs(UnmanagedType.Bool)]
  private static extern bool UnhookWindowsHookEx(IntPtr hhk);

  [DllImport("user32.dll")]
  private static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);

  [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
  private static extern IntPtr GetModuleHandle(string lpModuleName);

  public static void Start() {
    using (Process process = Process.GetCurrentProcess())
    using (ProcessModule module = process.MainModule) {
      IntPtr moduleHandle = GetModuleHandle(module.ModuleName);
      keyboardHook = SetWindowsHookEx(WH_KEYBOARD_LL, keyboardProc, moduleHandle, 0);
      mouseHook = SetWindowsHookEx(WH_MOUSE_LL, mouseProc, moduleHandle, 0);
    }
  }

  public static void Stop() {
    if (keyboardHook != IntPtr.Zero) UnhookWindowsHookEx(keyboardHook);
    if (mouseHook != IntPtr.Zero) UnhookWindowsHookEx(mouseHook);
  }

  public static string SnapshotJson() {
    lock (gate) {
      return String.Format(
        "{{\\"key_count\\":{0},\\"mouse_distance\\":{1:0},\\"click_count\\":{2},\\"scroll_count\\":{3}}}",
        keyCount,
        mouseDistance,
        clickCount,
        scrollCount
      );
    }
  }

  private static IntPtr KeyboardCallback(int nCode, IntPtr wParam, IntPtr lParam) {
    if (nCode >= 0) {
      int message = wParam.ToInt32();
      if (message == WM_KEYDOWN || message == WM_SYSKEYDOWN) {
        lock (gate) keyCount += 1;
      }
    }
    return CallNextHookEx(keyboardHook, nCode, wParam, lParam);
  }

  private static IntPtr MouseCallback(int nCode, IntPtr wParam, IntPtr lParam) {
    if (nCode >= 0) {
      int message = wParam.ToInt32();
      MSLLHOOKSTRUCT data = (MSLLHOOKSTRUCT)Marshal.PtrToStructure(lParam, typeof(MSLLHOOKSTRUCT));
      lock (gate) {
        if (message == WM_MOUSEMOVE) {
          if (lastX != Int32.MinValue && lastY != Int32.MinValue) {
            int dx = data.pt.x - lastX;
            int dy = data.pt.y - lastY;
            mouseDistance += Math.Sqrt((dx * dx) + (dy * dy));
          }
          lastX = data.pt.x;
          lastY = data.pt.y;
        } else if (message == WM_LBUTTONDOWN || message == WM_RBUTTONDOWN || message == WM_MBUTTONDOWN || message == WM_XBUTTONDOWN) {
          clickCount += 1;
        } else if (message == WM_MOUSEWHEEL || message == WM_MOUSEHWHEEL) {
          scrollCount += 1;
        }
      }
    }
    return CallNextHookEx(mouseHook, nCode, wParam, lParam);
  }
}
"@ -ReferencedAssemblies System.Windows.Forms
[InputMonitor]::Start()
try {
  while ($true) {
    [System.Windows.Forms.Application]::DoEvents()
    [Console]::WriteLine([InputMonitor]::SnapshotJson())
    [Console]::Out.Flush()
    Start-Sleep -Milliseconds 250
  }
} finally {
  [InputMonitor]::Stop()
}
`;
  inputMonitorProcess = spawn("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], {
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  inputMonitorProcess.stdout.on("data", (data) => {
    String(data)
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .forEach((line) => {
        try {
          const snapshot = JSON.parse(line);
          globalInputTotals = {
            key_count: Number(snapshot.key_count) || 0,
            mouse_distance: Number(snapshot.mouse_distance) || 0,
            click_count: Number(snapshot.click_count) || 0,
            scroll_count: Number(snapshot.scroll_count) || 0,
          };
        } catch (_) {
          // Ignore partial stdout chunks.
        }
      });
  });
  inputMonitorProcess.stderr.on("data", (data) => console.error(`[input-monitor] ${data}`));
  inputMonitorProcess.on("exit", () => {
    inputMonitorProcess = null;
  });
}

function stopGlobalInputMonitor() {
  if (!inputMonitorProcess) return;
  inputMonitorProcess.kill();
  inputMonitorProcess = null;
}

ipcMain.handle("desktop:list-sources", async () => {
  const displaysById = new Map(screen.getAllDisplays().map((display) => [String(display.id), display]));
  const sources = await desktopCapturer.getSources({
    types: ["window", "screen"],
    thumbnailSize: { width: 420, height: 260 },
  });
  return sources.map((source) => ({
    id: source.id,
    name:
      source.display_id && displaysById.has(String(source.display_id))
        ? `${source.name} / Windows display ${source.display_id}`
        : source.name,
    displayId: source.display_id || "",
    thumbnail: source.thumbnail.toDataURL(),
  }));
});

ipcMain.handle("desktop:capture-source", async (_event, sourceId) => {
  const sources = await desktopCapturer.getSources({
    types: ["window", "screen"],
    thumbnailSize: { width: 1600, height: 1000 },
  });
  const source = sources.find((item) => item.id === sourceId);
  if (!source) {
    throw new Error(`Capture source not found: ${sourceId}`);
  }
  return { sourceId, dataUrl: source.thumbnail.toDataURL() };
});

ipcMain.handle("desktop:capture-primary-screen", async () => {
  const sources = await desktopCapturer.getSources({
    types: ["screen"],
    thumbnailSize: { width: 1920, height: 1080 },
  });
  const source = sources[0];
  if (!source) {
    throw new Error("No screen source is available");
  }
  return { sourceId: source.id, sourceName: source.name, dataUrl: source.thumbnail.toDataURL() };
});

ipcMain.handle("desktop:read-global-input", async () => {
  startGlobalInputMonitor();
  const delta = subtractInputSnapshot(globalInputTotals, lastInputReadTotals);
  lastInputReadTotals = { ...globalInputTotals };
  return delta;
});

ipcMain.handle("desktop:get-active-window", async () => {
  const script = `
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class Win32 {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
}
"@
$h = [Win32]::GetForegroundWindow()
$b = New-Object System.Text.StringBuilder 512
[void][Win32]::GetWindowText($h, $b, $b.Capacity)
$pidValue = 0
[void][Win32]::GetWindowThreadProcessId($h, [ref]$pidValue)
$p = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
[PSCustomObject]@{ title = $b.ToString(); processName = if ($p) { $p.ProcessName } else { "" } } | ConvertTo-Json -Compress
`;
  return new Promise((resolve) => {
    execFile("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], (error, stdout) => {
      if (error) {
        resolve({ title: "", processName: "" });
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch (_) {
        resolve({ title: "", processName: "" });
      }
    });
  });
});

app.whenReady().then(async () => {
  startGlobalInputMonitor();
  if (process.env.EEG_BACKEND_URL) {
    backendUrl = process.env.EEG_BACKEND_URL;
    runtimeToken = process.env.EEG_RUNTIME_TOKEN || "";
  } else {
    await startBackend();
  }
  await createWindow();
});

app.on("window-all-closed", () => {
  stopGlobalInputMonitor();
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  stopGlobalInputMonitor();
  if (backendProcess) backendProcess.kill();
});

