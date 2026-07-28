[CmdletBinding()]
param(
    [string]$Plan = (Join-Path $PSScriptRoot "maintenance_plan.example.json"),
    [string]$WorkspaceRoot,
    [string]$SessionDir,
    [string]$SessionName = "maintenance",
    [double]$DurationHours = 10.0,
    [int]$PollMs = 5000,
    [string]$StopFile,
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"

function Resolve-WorkspacePath {
    param(
        [Parameter(Mandatory = $true)][string]$PathValue,
        [Parameter(Mandatory = $true)][string]$BasePath
    )

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return (Resolve-Path -LiteralPath $PathValue).Path
    }

    return [System.IO.Path]::GetFullPath((Join-Path $BasePath $PathValue))
}

$repoRoot = if ($WorkspaceRoot) {
    (Resolve-Path -LiteralPath $WorkspaceRoot).Path
} else {
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

$planPath = (Resolve-Path -LiteralPath $Plan).Path

if ($SessionDir) {
    $sessionRoot = Resolve-WorkspacePath -PathValue $SessionDir -BasePath $repoRoot
} else {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $safeName = ($SessionName -replace "[^a-zA-Z0-9._-]", "-")
    $sessionRoot = Join-Path $repoRoot ".tmp\maintenance_sessions\$timestamp-$safeName"
}

New-Item -ItemType Directory -Force -Path $sessionRoot | Out-Null

$python = Join-Path $repoRoot ".venv311\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

$args = @(
    "-m", "audio_processor", "maintenance",
    "--plan", $planPath,
    "--workspace-root", $repoRoot,
    "--session-dir", $sessionRoot,
    "--duration-hours", "$DurationHours",
    "--poll-ms", "$PollMs"
)
if ($StopFile) {
    $args += @("--stop-file", (Resolve-WorkspacePath -PathValue $StopFile -BasePath $repoRoot))
}

if ($Foreground) {
    & $python @args
    exit $LASTEXITCODE
}

$stdout = Join-Path $sessionRoot "worker.stdout.log"
$stderr = Join-Path $sessionRoot "worker.stderr.log"
$process = Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr

$payload = @{
    format = "vocal_process_maintenance_session_launch_v1"
    process_id = $process.Id
    workspace_root = $repoRoot
    plan_path = $planPath
    session_dir = $sessionRoot
    stdout_log = $stdout
    stderr_log = $stderr
    duration_hours = $DurationHours
    poll_ms = $PollMs
}

Write-Output (ConvertTo-Json $payload -Depth 4)
