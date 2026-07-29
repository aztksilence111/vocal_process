param(
    [string]$Root = "tests_real",
    [string]$OutputRoot = "tests_real\output",
    [string]$SourceSeparation = "never"
)

$ErrorActionPreference = "Stop"
$env:VOCAL_PROCESS_ASR_BACKEND = "whisperx"
$env:VOCAL_PROCESS_ALLOW_MODEL_DOWNLOAD = "1"

$python = Join-Path $PSScriptRoot "..\.venv311\Scripts\python.exe"
& $python -m audio_processor.real_eval `
    --root $Root `
    --render `
    --source-separation $SourceSeparation `
    --output-root $OutputRoot

exit $LASTEXITCODE
