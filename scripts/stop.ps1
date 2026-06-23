$ErrorActionPreference = "SilentlyContinue"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidFile = Join-Path $root "data\server.pid"
$ports = @((8765..8780) + 5173 + 9223)

function Get-CurrentProcessTree {
    $tree = @{}
    $currentPid = $PID
    while ($currentPid) {
        $tree[[int]$currentPid] = $true
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $currentPid"
        if (-not $process -or -not $process.ParentProcessId -or $tree.ContainsKey([int]$process.ParentProcessId)) {
            break
        }
        $currentPid = [int]$process.ParentProcessId
    }
    return $tree
}

function Stop-Tree {
    param([int]$ProcessId, [hashtable]$CurrentTree)
    if (-not $ProcessId -or $CurrentTree.ContainsKey($ProcessId)) {
        return
    }
    Stop-Process -Id $ProcessId -Force
    & taskkill.exe /PID $ProcessId /T /F > $null 2> $null
}

function Get-AllPortOwnerIds {
    $owners = @()
    $portSet = @{}
    foreach ($port in $ports) {
        $portSet[[int]$port] = $true
    }

    $owners += Get-NetTCPConnection -State Listen |
        Where-Object { $portSet.ContainsKey([int]$_.LocalPort) } |
        Select-Object -ExpandProperty OwningProcess -Unique

    $netstat = & netstat.exe -ano -p tcp
    foreach ($line in $netstat) {
        if ($line -match "^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$" -and $portSet.ContainsKey([int]$Matches[1])) {
            $owners += [int]$Matches[1]
        }
    }

    return $owners | Where-Object { $_ -and [int]$_ -gt 0 } | Sort-Object -Unique
}

function Get-ListeningPorts {
    $listening = @()
    $listening += Get-NetTCPConnection -State Listen |
        Where-Object { $ports -contains [int]$_.LocalPort } |
        Select-Object -ExpandProperty LocalPort -Unique

    $netstat = & netstat.exe -ano -p tcp
    foreach ($line in $netstat) {
        if ($line -match "^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+\d+\s*$" -and $ports -contains [int]$Matches[1]) {
            $listening += [int]$Matches[1]
        }
    }
    return $listening | Sort-Object -Unique
}

function Get-RuntimeOwnerIds {
    $owners = @()
    foreach ($port in (Get-ListeningPorts)) {
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/runtime" -TimeoutSec 1
            if ($response.pid) {
                $owners += [int]$response.pid
            }
        } catch {
        }
    }
    return $owners | Where-Object { $_ -and [int]$_ -gt 0 } | Sort-Object -Unique
}

$currentTree = Get-CurrentProcessTree

if (Test-Path $pidFile) {
    $pidValue = Get-Content $pidFile | Select-Object -First 1
    if ($pidValue) {
        Stop-Tree -ProcessId ([int]$pidValue) -CurrentTree $currentTree
    }
    Remove-Item $pidFile -Force
}

Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    $_.CommandLine.Contains($root) -and
    -not $currentTree.ContainsKey([int]$_.ProcessId) -and
    $_.Name -in @("python.exe", "node.exe", "electron.exe", "uv.exe", "eeg-task-scheduler.exe")
} | ForEach-Object {
    Stop-Tree -ProcessId ([int]$_.ProcessId) -CurrentTree $currentTree
}

for ($attempt = 1; $attempt -le 3; $attempt++) {
    Get-RuntimeOwnerIds | ForEach-Object {
        Stop-Tree -ProcessId ([int]$_) -CurrentTree $currentTree
    }
    Get-AllPortOwnerIds | ForEach-Object {
        Stop-Tree -ProcessId ([int]$_) -CurrentTree $currentTree
    }
    Start-Sleep -Milliseconds 350
}

$remaining = @()
Get-NetTCPConnection -State Listen | Where-Object { $ports -contains [int]$_.LocalPort } | ForEach-Object {
    if (-not $currentTree.ContainsKey([int]$_.OwningProcess)) {
        $remaining += "$($_.LocalPort):$($_.OwningProcess)"
    }
}
$netstat = & netstat.exe -ano -p tcp
foreach ($line in $netstat) {
    if ($line -match "^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$" -and $ports -contains [int]$Matches[1]) {
        if (-not $currentTree.ContainsKey([int]$Matches[2])) {
            $remaining += "$($Matches[1]):$($Matches[2])"
        }
    }
}
$remaining = $remaining | Sort-Object -Unique

if ($remaining.Count) {
    Write-Warning "Some ports are still occupied after cleanup: $($remaining -join ', ')"
    Write-Warning "If these are invisible/elevated processes, restart the terminal as administrator or reboot Windows."
} else {
    Write-Host "Stopped eeg-task-scheduler processes and cleared dev ports."
}

exit 0
