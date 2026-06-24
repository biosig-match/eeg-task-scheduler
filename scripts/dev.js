#!/usr/bin/env node
const { spawnSync, spawn } = require("child_process");
const net = require("net");
const crypto = require("crypto");
const path = require("path");

const root = path.resolve(__dirname, "..");

if (process.platform === "win32") {
  const result = spawnSync(
    "powershell",
    ["-ExecutionPolicy", "Bypass", "-File", path.join(__dirname, "dev.ps1")],
    { stdio: "inherit", cwd: root }
  );
  process.exit(result.status ?? 0);
}

// macOS / Linux
async function findFreePort(start = 8766, end = 8780) {
  for (let port = start; port <= end; port++) {
    const free = await new Promise((resolve) => {
      const s = net.createServer();
      s.once("error", () => resolve(false));
      s.once("listening", () => s.close(() => resolve(true)));
      s.listen(port, "127.0.0.1");
    });
    if (free) return port;
  }
  throw new Error(`No free port found in ${start}-${end}`);
}

(async () => {
  const backendPort = await findFreePort();
  const backendUrl = `http://127.0.0.1:${backendPort}`;
  const webDevUrl = "http://127.0.0.1:5173";
  const runtimeToken = crypto.randomUUID().replaceAll("-", "");

  const env = {
    ...process.env,
    EEG_BACKEND_URL: backendUrl,
    EEG_WEB_DEV_URL: webDevUrl,
    VITE_EEG_BACKEND_URL: backendUrl,
    EEG_RUNTIME_TOKEN: runtimeToken,
    VITE_EEG_RUNTIME_TOKEN: runtimeToken,
  };

  console.log(`Using backend ${backendUrl}`);

  const proc = spawn(
    "npx",
    [
      "concurrently", "-k", "-n", "backend,web,desktop",
      `uv run eeg-task-scheduler --reload --port ${backendPort}`,
      "npm --prefix web run dev",
      "npm --prefix desktop run dev",
    ],
    { stdio: "inherit", cwd: root, env, shell: false }
  );

  proc.on("exit", (code) => process.exit(code ?? 0));
})();
