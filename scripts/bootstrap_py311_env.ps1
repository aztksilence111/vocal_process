param(
    [string]$Python = "",
    [switch]$Recreate,
    [switch]$DevTools,
    [switch]$FullModelRuntime
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvRoot = Join-Path $ProjectRoot ".venv311"

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

function Resolve-Python311 {
    if ($Python) {
        if (-not (Test-Path -LiteralPath $Python)) {
            throw "Python 3.11 executable not found: $Python"
        }
        return (Resolve-Path -LiteralPath $Python).Path
    }

    $Candidates = @(
        "C:\Python311\python.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:ProgramFiles "Python311\python.exe")
    )

    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }

    $Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($Launcher) {
        $Probe = & py -3.11 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $Probe) {
            return $Probe.Trim()
        }
    }

    throw "Python 3.11 was not found. Install it with: choco install python311 -y"
}

function Assert-Python311 {
    param([string]$PythonExe)

    $Version = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not run Python: $PythonExe"
    }
    if (-not $Version.StartsWith("3.11.")) {
        throw "Main runtime must use Python 3.11.x, got $Version from $PythonExe"
    }
}

$PythonExe = Resolve-Python311
Assert-Python311 $PythonExe

if ($Recreate.IsPresent -and (Test-Path -LiteralPath $VenvRoot)) {
    Assert-ProjectChildPath $VenvRoot "Python 3.11 virtual environment"
    Remove-Item -LiteralPath $VenvRoot -Recurse -Force
}

if (-not (Test-Path -LiteralPath (Join-Path $VenvRoot "Scripts\python.exe"))) {
    & $PythonExe -m venv $VenvRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create Python 3.11 virtual environment"
    }
}

$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
& $VenvPython -m pip install --disable-pip-version-check --progress-bar off --retries 10 --timeout 120 --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade Python 3.11 packaging tools"
}

& $VenvPython -m pip install --disable-pip-version-check --progress-bar off --retries 10 --timeout 120 -r (Join-Path $ProjectRoot "requirements\main-py311.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install main Python 3.11 runtime requirements"
}

if ($DevTools.IsPresent) {
    & $VenvPython -m pip install --disable-pip-version-check --progress-bar off --retries 10 --timeout 120 -r (Join-Path $ProjectRoot "requirements\dev-py311.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Python 3.11 development tools"
    }
}

if ($FullModelRuntime.IsPresent) {
    & $VenvPython -m pip install --disable-pip-version-check --progress-bar off --retries 10 --timeout 120 -r (Join-Path $ProjectRoot "requirements\model-full-py311.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install full Python 3.11 model runtime"
    }
}

Write-Host "Python 3.11 environment ready:"
Write-Host $VenvPython
