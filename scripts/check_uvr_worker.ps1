param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Python) {
    $Python = Join-Path $ProjectRoot ".uvr-worker\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "UVR worker Python not found: $Python. Run scripts\bootstrap_uvr_worker.ps1 first."
}

$ScriptsRoot = Join-Path (Split-Path -Parent (Split-Path -Parent $Python)) "Scripts"
$RunnerCommands = @(
    (Join-Path $ScriptsRoot "uvr-demucs.exe"),
    (Join-Path $ScriptsRoot "uvr-mdx.exe"),
    (Join-Path $ScriptsRoot "uvr-vr.exe")
)

Write-Host "UVR worker Python:"
& $Python -c "import sys; print(sys.version); print(sys.executable)"
if ($LASTEXITCODE -ne 0) {
    throw "UVR worker Python failed"
}

Write-Host "UVR package:"
& $Python -m pip show uvr-headless-runner
if ($LASTEXITCODE -ne 0) {
    throw "uvr-headless-runner is not installed"
}

Write-Host "Runner module probe:"
$ProbeCode = @'
import importlib.util
import json
modules = ["demucs_headless_runner", "mdx_headless_runner", "vr_headless_runner"]
print(json.dumps({name: importlib.util.find_spec(name).origin for name in modules}, indent=2))
'@
$TmpRoot = Join-Path $ProjectRoot ".tmp"
New-Item -ItemType Directory -Force -Path $TmpRoot | Out-Null
$ProbeFile = Join-Path $TmpRoot "check_uvr_worker_probe.py"
$ProbeCode | Set-Content -LiteralPath $ProbeFile -Encoding UTF8
& $Python $ProbeFile
if ($LASTEXITCODE -ne 0) {
    throw "UVR runner module probe failed"
}

foreach ($Runner in $RunnerCommands) {
    if (-not (Test-Path -LiteralPath $Runner)) {
        throw "UVR runner command not found: $Runner"
    }
    Write-Host "Checking installed models with $(Split-Path -Leaf $Runner):"
    & $Runner --list-installed
    if ($LASTEXITCODE -ne 0) {
        throw "UVR runner command failed: $Runner"
    }
}

Write-Host "Default model info (htdemucs):"
& (Join-Path $ScriptsRoot "uvr-demucs.exe") --model-info htdemucs
if ($LASTEXITCODE -ne 0) {
    throw "UVR default model info failed"
}

Write-Host "UVR worker check passed."
