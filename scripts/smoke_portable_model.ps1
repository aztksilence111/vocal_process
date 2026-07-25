param(
    [string]$AppName = "VocalProcess",
    [string]$PortableRoot = "",
    [string]$WorkRoot = "",
    [int]$MinimumDurationSeconds = 1
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $PortableRoot) {
    $PortableRoot = Join-Path $ProjectRoot "dist\$AppName-portable\$AppName"
}
if (-not $WorkRoot) {
    $WorkRoot = Join-Path $ProjectRoot ".tmp\portable-model-smoke"
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

function New-SpeechWave {
    param(
        [string]$Path,
        [string]$Text
    )

    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        $synth.SetOutputToWaveFile($Path)
        $synth.Speak($Text)
    }
    finally {
        $synth.Dispose()
    }
}

$AppRoot = (Resolve-Path -LiteralPath $PortableRoot).Path
$ExePath = Join-Path $AppRoot "$AppName.exe"
$ModelRoot = Join-Path $AppRoot "models"
$FfprobePath = Join-Path $AppRoot "bin\ffprobe.exe"
$TclDataPath = Join-Path $AppRoot "_internal\_tcl_data"
$TkDataPath = Join-Path $AppRoot "_internal\_tk_data"

foreach ($Path in @($ExePath, $ModelRoot, $FfprobePath, $TclDataPath, $TkDataPath)) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required portable path not found: $Path"
    }
}

Assert-ProjectChildPath $WorkRoot "Portable model smoke-test directory"
if (Test-Path -LiteralPath $WorkRoot) {
    Remove-Item -LiteralPath $WorkRoot -Recurse -Force
}

$MaterialsRoot = Join-Path $WorkRoot "materials"
$OutputRoot = Join-Path $WorkRoot "out"
New-Item -ItemType Directory -Force -Path $MaterialsRoot | Out-Null
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

Add-Type -AssemblyName System.Speech
New-SpeechWave (Join-Path $WorkRoot "reference.wav") "Hello world. This is a test for vocal process."
New-SpeechWave (Join-Path $MaterialsRoot "001.wav") "Hello world."
New-SpeechWave (Join-Path $MaterialsRoot "002.wav") "This is a test."
New-SpeechWave (Join-Path $MaterialsRoot "003.wav") "For vocal process."

$PreviousModelCache = $env:VOCAL_PROCESS_MODEL_CACHE
$env:VOCAL_PROCESS_MODEL_CACHE = $ModelRoot
try {
    $Arguments = @(
        "batch",
        (Join-Path $WorkRoot "reference.wav"),
        (Join-Path $OutputRoot "reference.wav"),
        "--material-directory",
        $MaterialsRoot,
        "--overwrite"
    )
    $Process = Start-Process -FilePath $ExePath -ArgumentList $Arguments -PassThru -WindowStyle Hidden
    $Deadline = (Get-Date).AddSeconds(420)
    while (-not $Process.HasExited -and (Get-Date) -lt $Deadline) {
        Start-Sleep -Seconds 2
        $Process.Refresh()
    }
    if (-not $Process.HasExited) {
        $Process.Kill()
        throw "$AppName.exe batch did not finish within the portable model smoke-test timeout"
    }
    if ($Process.ExitCode -ne 0) {
        throw "$AppName.exe batch returned exit code $($Process.ExitCode)"
    }
}
finally {
    $env:VOCAL_PROCESS_MODEL_CACHE = $PreviousModelCache
}

$OutputPath = Join-Path $OutputRoot "reference.wav"
$DiagnosticsPath = Join-Path $OutputRoot "reference.diagnostics.jsonl"
for ($Attempt = 0; $Attempt -lt 120; $Attempt++) {
    if ((Test-Path -LiteralPath $OutputPath) -and (Test-Path -LiteralPath $DiagnosticsPath)) {
        break
    }
    Start-Sleep -Seconds 1
}
foreach ($Path in @($OutputPath, $DiagnosticsPath)) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Expected model smoke-test output not found: $Path"
    }
}

$ProbeJson = & $FfprobePath -v error -show_entries format=duration,format_name -show_entries stream=codec_name,sample_rate,channels -of json $OutputPath
$Probe = $ProbeJson | ConvertFrom-Json
$Duration = [double]$Probe.format.duration
if ($Duration -lt $MinimumDurationSeconds) {
    throw "Portable model smoke-test output is too short: $Duration seconds"
}

$DiagnosticsText = Get-Content -LiteralPath $DiagnosticsPath -Raw -Encoding UTF8
foreach ($Pattern in @("model.ordering.completed", "batch.item.completed", "reference vocals separated with demucs")) {
    if ($DiagnosticsText -notmatch [regex]::Escape($Pattern)) {
        throw "Diagnostics did not contain expected model stage: $Pattern"
    }
}
if ($DiagnosticsText -match "batch.item.failed|demucs separation failed") {
    throw "Diagnostics contain a failed processing stage"
}

Write-Host "Portable model smoke test passed:"
Write-Host $OutputPath
