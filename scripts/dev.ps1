$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$candidatePorts = 8766..8780

function Test-PortAvailable {
    param([int]$Port)
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse("127.0.0.1"), $Port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) {
            $listener.Stop()
        }
    }
}

$backendPort = $null
foreach ($port in $candidatePorts) {
    if (Test-PortAvailable -Port $port) {
        $backendPort = $port
        break
    }
}

if (-not $backendPort) {
    throw "No free backend port found in $($candidatePorts[0])-$($candidatePorts[-1])."
}

$env:EEG_BACKEND_URL = "http://127.0.0.1:$backendPort"
$env:EEG_WEB_DEV_URL = "http://127.0.0.1:5173"
$env:VITE_EEG_BACKEND_URL = $env:EEG_BACKEND_URL
$env:EEG_RUNTIME_TOKEN = [guid]::NewGuid().ToString("N")
$env:VITE_EEG_RUNTIME_TOKEN = $env:EEG_RUNTIME_TOKEN

Write-Host "Using backend $env:EEG_BACKEND_URL"

& npx concurrently -k -n backend,web,desktop `
    "uv run eeg-task-scheduler --reload --port $backendPort" `
    "npm --prefix web run dev" `
    "npm --prefix desktop run dev"
