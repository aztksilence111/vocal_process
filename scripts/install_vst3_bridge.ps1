param(
    [string]$PluginPath = "build\vst3_bridge\VocalProcessBridge_artefacts\Release\VST3\VocalProcess Bridge.vst3",
    [string]$InstallRoot = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $ProjectRoot

if (-not $InstallRoot) {
    $InstallRoot = Join-Path $env:CommonProgramFiles "VST3"
}

$ResolvedPlugin = Resolve-Path -LiteralPath $PluginPath
if (-not (Test-Path -LiteralPath (Join-Path $ResolvedPlugin.Path "Contents\x86_64-win"))) {
    throw "VST3 bundle does not look valid: $($ResolvedPlugin.Path)"
}

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null

$Destination = Join-Path $InstallRoot (Split-Path -Leaf $ResolvedPlugin.Path)
if ((Test-Path -LiteralPath $Destination) -and (-not $Force)) {
    throw "Destination already exists: $Destination. Re-run with -Force to replace it."
}

if (Test-Path -LiteralPath $Destination) {
    Remove-Item -LiteralPath $Destination -Recurse -Force
}

Copy-Item -LiteralPath $ResolvedPlugin.Path -Destination $Destination -Recurse -Force

Write-Host "Installed VST3 bridge:"
Write-Host $Destination
