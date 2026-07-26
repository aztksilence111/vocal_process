param(
    [string]$Python = "",
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$WorkerRoot = Join-Path $ProjectRoot ".uvr-worker"

function Assert-ProjectChildPath {
    param(
        [string]$PathToCheck,
        [string]$Label
    )

    $Root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $FullPath = [System.IO.Path]::GetFullPath($PathToCheck)
    $RootPrefix = "$Root\"
    if (-not $FullPath.StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must stay under project root: $FullPath"
    }
}

function Resolve-Python310 {
    if ($Python) {
        if (-not (Test-Path -LiteralPath $Python)) {
            throw "Python 3.10 executable not found: $Python"
        }
        return (Resolve-Path -LiteralPath $Python).Path
    }

    $Candidates = @(
        "C:\Python310\python.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python310\python.exe"),
        (Join-Path $env:ProgramFiles "Python310\python.exe")
    )

    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }

    $Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($Launcher) {
        $Probe = & py -3.10 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $Probe) {
            return $Probe.Trim()
        }
    }

    throw "Python 3.10 was not found. Install it with: choco install python310 -y"
}

function Assert-Python310 {
    param([string]$PythonExe)

    $Version = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not run Python: $PythonExe"
    }
    if (-not $Version.StartsWith("3.10.")) {
        throw "UVR worker must use Python 3.10.x, got $Version from $PythonExe"
    }
}

if ($Recreate.IsPresent -and (Test-Path -LiteralPath $WorkerRoot)) {
    Assert-ProjectChildPath $WorkerRoot "UVR worker virtual environment"
    Remove-Item -LiteralPath $WorkerRoot -Recurse -Force
}

$PythonExe = Resolve-Python310
Assert-Python310 $PythonExe

if (-not (Test-Path -LiteralPath (Join-Path $WorkerRoot "Scripts\python.exe"))) {
    & $PythonExe -m venv $WorkerRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create UVR worker virtual environment"
    }
}

$WorkerPython = Join-Path $WorkerRoot "Scripts\python.exe"
& $WorkerPython -m pip install --disable-pip-version-check --progress-bar off --retries 10 --timeout 120 --upgrade pip wheel
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade UVR worker packaging tools"
}

& $WorkerPython -m pip install --disable-pip-version-check --progress-bar off --retries 10 --timeout 120 -r (Join-Path $ProjectRoot "requirements\uvr-worker-py310.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install UVR worker requirements"
}

$TmpRoot = Join-Path $ProjectRoot ".tmp"
New-Item -ItemType Directory -Force -Path $TmpRoot | Out-Null
$ProbeFile = Join-Path $TmpRoot "probe_uvr_worker.py"
$ProbeCode = @'
import importlib.util
import json
import sys

modules = ["demucs_headless_runner", "mdx_headless_runner", "vr_headless_runner"]
result = {}
for module in modules:
    spec = importlib.util.find_spec(module)
    result[module] = spec.origin if spec and spec.origin else None
print(json.dumps(result, ensure_ascii=False, indent=2))
if not any(result.values()):
    sys.exit(1)
'@

$ProbeCode | Set-Content -LiteralPath $ProbeFile -Encoding UTF8
$ProbeOutput = & $WorkerPython $ProbeFile
if ($LASTEXITCODE -ne 0) {
    throw "UVR runner modules were not found after installation"
}

$EnvFile = Join-Path $TmpRoot "uvr-worker-env.ps1"
@(
    "`$env:VOCAL_PROCESS_UVR_PYTHON = '$WorkerPython'"
    "`$env:VOCAL_PROCESS_SOURCE_SEPARATOR = 'auto'"
    "`$env:VOCAL_PROCESS_UVR_ARCH = 'demucs'"
    "`$env:VOCAL_PROCESS_UVR_MODEL = 'htdemucs'"
) | Set-Content -LiteralPath $EnvFile -Encoding UTF8

Write-Host "UVR worker ready:"
Write-Host $WorkerPython
Write-Host "Runner modules:"
$ProbeOutput | ForEach-Object { Write-Host $_ }
Write-Host "Environment helper:"
Write-Host $EnvFile
