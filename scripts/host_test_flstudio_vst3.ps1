param(
    [string]$PluginManagerExe = "G:\Image-Line\FL Studio 2024\System\Tools\Plugin Manager\PluginManager.exe",
    [string]$DatabaseRoot = "",
    [int]$WaitSeconds = 20
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath $PluginManagerExe)) {
    throw "FL Studio Plugin Manager not found: $PluginManagerExe"
}

if (-not $DatabaseRoot) {
    $DatabaseRoot = Join-Path $env:USERPROFILE "Documents\Image-Line\FL Studio\Presets\Plugin database\Installed"
}

Write-Host "Starting FL Studio Plugin Manager:"
Write-Host $PluginManagerExe
Write-Host "Checking database root:"
Write-Host $DatabaseRoot

$BeforeMatches = @()
if (Test-Path -LiteralPath $DatabaseRoot) {
    $BeforeMatches = @(Select-String -Path (Join-Path $DatabaseRoot "VerifiedIDs.nfo") -Pattern "ABCDEF01-9182-FAEB-5650-727356706231|VocalProcess" -CaseSensitive:$false -ErrorAction SilentlyContinue)
}

$Process = Start-Process -FilePath $PluginManagerExe -PassThru -WindowStyle Minimized
Start-Sleep -Seconds $WaitSeconds

if (-not $Process.HasExited) {
    [void]$Process.CloseMainWindow()
    Start-Sleep -Seconds 3
}

if (-not $Process.HasExited) {
    Stop-Process -Id $Process.Id -Force
}

$AfterMatches = @()
if (Test-Path -LiteralPath $DatabaseRoot) {
    $AfterMatches = @(Select-String -Path (Join-Path $DatabaseRoot "VerifiedIDs.nfo") -Pattern "ABCDEF01-9182-FAEB-5650-727356706231|VocalProcess" -CaseSensitive:$false -ErrorAction SilentlyContinue)
}

if ($AfterMatches.Count -gt 0) {
    Write-Host "FL Studio database contains VocalProcess Bridge:"
    $AfterMatches | ForEach-Object { Write-Host $_.Line }
    exit 0
}

if ($BeforeMatches.Count -eq 0) {
    Write-Host "FL Studio Plugin Manager launched, but no automatic scan result was written for VocalProcess Bridge."
    Write-Host "Use Plugin Manager > Find installed plugins after installing the VST3 into the common VST3 folder."
    exit 2
}

Write-Host "FL Studio database state did not change."
exit 2
