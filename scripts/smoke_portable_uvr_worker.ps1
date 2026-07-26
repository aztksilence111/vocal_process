param(
    [string]$AppName = "VocalProcess",
    [string]$PortableRoot = "",
    [string]$WorkRoot = "",
    [int]$TimeoutSeconds = 420
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $PortableRoot) {
    $PortableRoot = Join-Path $ProjectRoot "dist\$AppName-portable\$AppName"
}
if (-not $WorkRoot) {
    $WorkRoot = Join-Path $ProjectRoot ".tmp\portable-uvr-smoke"
}

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

$AppRoot = (Resolve-Path -LiteralPath $PortableRoot).Path
$WorkerPython = Join-Path $AppRoot "uvr-worker\Scripts\python.exe"
$UvrDemucs = Join-Path $AppRoot "uvr-worker\Scripts\uvr-demucs.exe"
$Ffmpeg = Join-Path $AppRoot "bin\ffmpeg.exe"
foreach ($Path in @($WorkerPython, $UvrDemucs, $Ffmpeg)) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required portable UVR path not found: $Path"
    }
}

Assert-ProjectChildPath $WorkRoot "Portable UVR smoke-test directory"
if (Test-Path -LiteralPath $WorkRoot) {
    Remove-Item -LiteralPath $WorkRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null

$InputPath = Join-Path $WorkRoot "reference.wav"
$OutputRoot = Join-Path $WorkRoot "out"
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

& $Ffmpeg -hide_banner -y -f lavfi -i "sine=frequency=440:duration=1" -ar 44100 -ac 2 $InputPath
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create portable UVR smoke input"
}

$RunnerArgs = @(
    "--model", "htdemucs",
    "--input", $InputPath,
    "--output", $OutputRoot,
    "--cpu",
    "--stem", "Vocals",
    "--primary-only",
    "--wav-type", "PCM_24",
    "--quiet"
)
& $UvrDemucs @RunnerArgs
if ($LASTEXITCODE -ne 0) {
    throw "Portable UVR smoke test returned exit code $LASTEXITCODE"
}

$Vocals = Get-ChildItem -LiteralPath $OutputRoot -Recurse -File |
    Where-Object { $_.Name -match "Vocal|vocals" } |
    Select-Object -First 1
if (-not $Vocals) {
    throw "Portable UVR smoke test did not produce a vocals file"
}

Write-Host "Portable UVR smoke test passed:"
Write-Host $Vocals.FullName
