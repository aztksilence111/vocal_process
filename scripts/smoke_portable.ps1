param(
    [string]$AppName = "VocalProcess",
    [string]$ZipPath = "",
    [string]$ExtractRoot = "",
    [int]$TimeoutSeconds = 5,
    [switch]$ReuseExtract
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $ZipPath) {
    $ZipPath = Join-Path $ProjectRoot "dist\$AppName-portable.zip"
}
if (-not $ExtractRoot) {
    $ExtractRoot = Join-Path $ProjectRoot ".tmp\portable-smoke-test"
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

if (-not (Test-Path -LiteralPath $ZipPath)) {
    throw "Portable ZIP not found: $ZipPath"
}

Assert-ProjectChildPath $ExtractRoot "Smoke-test extraction directory"
if (-not $ReuseExtract) {
    if (Test-Path -LiteralPath $ExtractRoot) {
        Remove-Item -LiteralPath $ExtractRoot -Recurse -Force
    }

    New-Item -ItemType Directory -Force -Path $ExtractRoot | Out-Null
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $ExtractRoot -Force
}
elseif (-not (Test-Path -LiteralPath $ExtractRoot)) {
    throw "ReuseExtract was requested, but the extraction directory does not exist: $ExtractRoot"
}

$AppRoot = Join-Path $ExtractRoot $AppName
$ExePath = Join-Path $AppRoot "$AppName.exe"
$FfmpegPath = Join-Path $AppRoot "bin\ffmpeg.exe"
$FfprobePath = Join-Path $AppRoot "bin\ffprobe.exe"

foreach ($Path in @($AppRoot, $ExePath, $FfmpegPath, $FfprobePath)) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required portable file not found: $Path"
    }
}

& $FfmpegPath -version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Bundled ffmpeg.exe failed with exit code $LASTEXITCODE"
}

& $FfprobePath -version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Bundled ffprobe.exe failed with exit code $LASTEXITCODE"
}

$Process = Start-Process -FilePath $ExePath -PassThru -WindowStyle Hidden
Start-Sleep -Seconds $TimeoutSeconds

if ($Process.HasExited) {
    throw "$AppName.exe exited during smoke test with code $($Process.ExitCode)"
}

$Process.CloseMainWindow() | Out-Null
Start-Sleep -Seconds 1
if (-not $Process.HasExited) {
    $Process.Kill()
}

Write-Host "Portable smoke test passed:"
Write-Host $ZipPath
