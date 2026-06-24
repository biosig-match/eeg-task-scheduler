#!/usr/bin/env node
const { spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");

const root = path.resolve(__dirname, "..");
const ports = [...Array.from({ length: 15 }, (_, i) => 8766 + i), 5173, 9223];

if (process.platform === "win32") {
  const result = spawnSync(
    "powershell",
    ["-ExecutionPolicy", "Bypass", "-File", path.join(__dirname, "stop.ps1")],
    { stdio: "inherit" }
  );
  process.exit(result.status ?? 0);
}

// macOS / Linux
const pidFile = path.join(root, "data", "server.pid");

function tryKill(pid) {
  if (!pid || isNaN(pid)) return;
  try { process.kill(pid, "SIGTERM"); } catch (_) {}
}

if (fs.existsSync(pidFile)) {
  const pid = parseInt(fs.readFileSync(pidFile, "utf8").trim(), 10);
  tryKill(pid);
  try { fs.unlinkSync(pidFile); } catch (_) {}
}

for (const port of ports) {
  const r = spawnSync("lsof", ["-ti", `tcp:${port}`], { encoding: "utf8" });
  if (r.stdout) {
    for (const line of r.stdout.trim().split(/\r?\n/)) {
      tryKill(parseInt(line, 10));
    }
  }
}

const r = spawnSync("pgrep", ["-f", root], { encoding: "utf8" });
if (r.stdout) {
  for (const line of r.stdout.trim().split(/\r?\n/)) {
    tryKill(parseInt(line, 10));
  }
}

console.log("Stopped eeg-task-scheduler processes and cleared dev ports.");
process.exit(0);
