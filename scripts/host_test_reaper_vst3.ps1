param(
    [string]$ReaperExe = "D:\Program Files\REAPER (x64)\reaper.exe",
    [string]$PluginPath = "build\vst3_bridge\VocalProcessBridge_artefacts\Release\VST3\VocalProcess Bridge.vst3",
    [string]$WorkDir = ".tmp\reaper-vst3-host-test",
    [int]$ScanSeconds = 35
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath $ReaperExe)) {
    throw "REAPER executable not found: $ReaperExe"
}

$ResolvedPlugin = Resolve-Path -LiteralPath $PluginPath
$PluginSearchDir = Split-Path -Parent $ResolvedPlugin.Path
$ResolvedWorkDir = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $WorkDir))
$TempRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot ".tmp"))

if (-not $ResolvedWorkDir.StartsWith($TempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a REAPER test work directory outside $TempRoot"
}

New-Item -ItemType Directory -Force -Path $ResolvedWorkDir | Out-Null

foreach ($name in @("reaper-vstplugins64.ini", "reaper-vstshells64.ini", "reaper-vstrenames64.ini")) {
    $cachePath = Join-Path $ResolvedWorkDir $name
    if (Test-Path -LiteralPath $cachePath) {
        Remove-Item -LiteralPath $cachePath -Force
    }
}

$ConfigPath = Join-Path $ResolvedWorkDir "reaper.ini"
$ConfigLines = @(
    "[reaper]",
    "vstpath64=$PluginSearchDir",
    "vstpath=$PluginSearchDir",
    "splashfast=1",
    "lastcwd=$ProjectRoot",
    "[verchk]",
    "lastt=2000000000"
)
Set-Content -LiteralPath $ConfigPath -Encoding ASCII -Value $ConfigLines

Write-Host "Starting REAPER with isolated config:"
Write-Host $ConfigPath
Write-Host "Scanning VST3 directory:"
Write-Host $PluginSearchDir

$Process = Start-Process -FilePath $ReaperExe -ArgumentList @("-newinst", "-cfgfile", $ConfigPath, "-splashlog", "-new") -PassThru -WindowStyle Minimized
Start-Sleep -Seconds $ScanSeconds

if (-not $Process.HasExited) {
    [void]$Process.CloseMainWindow()
    Start-Sleep -Seconds 4
}

if (-not $Process.HasExited) {
    Stop-Process -Id $Process.Id -Force
}

$Cache = Join-Path $ResolvedWorkDir "reaper-vstplugins64.ini"
if (-not (Test-Path -LiteralPath $Cache)) {
    throw "REAPER did not create a VST cache at $Cache"
}

$Match = Select-String -LiteralPath $Cache -Pattern "VocalProcess|VocalProcess Bridge|VocalProcess_Bridge" -CaseSensitive:$false
if (-not $Match) {
    $SplashLogs = Get-ChildItem -LiteralPath $ResolvedWorkDir -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'splash|log|scan' }
    if ($SplashLogs) {
        Write-Host "REAPER diagnostic files:"
        $SplashLogs | Select-Object FullName,Length | Format-Table -AutoSize
    }
    Write-Host "REAPER VST cache:"
    Get-Content -LiteralPath $Cache
    throw "REAPER did not cache VocalProcess Bridge"
}

Write-Host "REAPER cached VocalProcess Bridge:"
$Match | ForEach-Object { Write-Host $_.Line }
