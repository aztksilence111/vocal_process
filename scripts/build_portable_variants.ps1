param(
    [string]$AppName = "VocalProcess",
    [switch]$SkipModelRuntime,
    [switch]$Lite,
    [switch]$AnalyzeModelRuntime,
    [switch]$BuildVst3Bridge,
    [string]$ModelCacheRoot = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildPortable = Join-Path $PSScriptRoot "build_portable.ps1"
$BuildVst3 = Join-Path $PSScriptRoot "build_vst3_bridge.ps1"
$Vst3BridgeRoot = Join-Path $ProjectRoot "build\vst3_bridge\VocalProcessBridge_artefacts\Release\VST3\VocalProcess Bridge.vst3"

if ($BuildVst3Bridge.IsPresent -or -not (Test-Path -LiteralPath $Vst3BridgeRoot)) {
    & powershell -ExecutionPolicy Bypass -File $BuildVst3
    if ($LASTEXITCODE -ne 0) {
        throw "VST3 bridge build failed"
    }
}

$CommonArgs = @("-ExecutionPolicy", "Bypass", "-File", $BuildPortable, "-AppName", $AppName)
$BuildLite = $Lite.IsPresent -or $SkipModelRuntime.IsPresent
if ($BuildLite) {
    $CommonArgs += @("-SkipModelRuntime", "-PackageSuffix", "lite")
}
if ($AnalyzeModelRuntime.IsPresent) {
    $CommonArgs += "-AnalyzeModelRuntime"
}
if ($ModelCacheRoot) {
    $CommonArgs += @("-ModelCacheRoot", $ModelCacheRoot)
}

& powershell @CommonArgs
if ($LASTEXITCODE -ne 0) {
    throw "Standard portable build failed"
}

& powershell @CommonArgs -IncludeVst3Bridge
if ($LASTEXITCODE -ne 0) {
    throw "VST3 portable build failed"
}

Write-Host "Portable package variants created:"
if ($BuildLite) {
    Write-Host (Join-Path $ProjectRoot "dist\$AppName-portable-lite.zip")
    Write-Host (Join-Path $ProjectRoot "dist\$AppName-portable-lite-vst3.zip")
} else {
    Write-Host (Join-Path $ProjectRoot "dist\$AppName-portable.zip")
    Write-Host (Join-Path $ProjectRoot "dist\$AppName-portable-vst3.zip")
}
