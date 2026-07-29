param(
    [string]$Root = "tests_real",
    [string]$OutputRoot = "tests_real\output",
    [string]$SourceSeparation = "never",
    [string]$StopFile = ""
)

$ErrorActionPreference = "Stop"

function Set-DefaultEnv {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($Name, "Process"))) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ProjectModelCache = Join-Path $ProjectRoot ".tmp\model-cache"

Set-DefaultEnv "VOCAL_PROCESS_MODEL_CACHE" $ProjectModelCache
Set-DefaultEnv "HF_HOME" $ProjectModelCache
Set-DefaultEnv "HUGGINGFACE_HUB_CACHE" (Join-Path $ProjectModelCache "hub")
Set-DefaultEnv "TORCH_HOME" (Join-Path $ProjectModelCache "torch")
Set-DefaultEnv "HF_ENDPOINT" "https://hf-mirror.com"
Set-DefaultEnv "HF_HUB_DISABLE_XET" "1"
Set-DefaultEnv "HF_HUB_ETAG_TIMEOUT" "60"
Set-DefaultEnv "HF_HUB_DOWNLOAD_TIMEOUT" "1800"
Set-DefaultEnv "VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD" "1"
Set-DefaultEnv "PYTHONIOENCODING" "utf-8"
Set-DefaultEnv "PYTHONUTF8" "1"

$env:VOCAL_PROCESS_ASR_BACKEND = "whisperx"

$python = Join-Path $PSScriptRoot "..\.venv311\Scripts\python.exe"
$realEvalArgs = @(
    "-m",
    "audio_processor.real_eval",
    "--root",
    $Root,
    "--render",
    "--source-separation",
    $SourceSeparation,
    "--output-root",
    $OutputRoot
)

$effectiveStopFile = $StopFile
if ([string]::IsNullOrWhiteSpace($effectiveStopFile)) {
    $effectiveStopFile = [Environment]::GetEnvironmentVariable("VOCAL_PROCESS_STOP_FILE", "Process")
}
if (-not [string]::IsNullOrWhiteSpace($effectiveStopFile)) {
    [Environment]::SetEnvironmentVariable("VOCAL_PROCESS_STOP_FILE", $effectiveStopFile, "Process")
    $realEvalArgs += @("--stop-file", $effectiveStopFile)
}

& $python @realEvalArgs

exit $LASTEXITCODE
