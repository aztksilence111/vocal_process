param(
    [string]$AppName = "VocalProcess"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Wrapper = Join-Path $ProjectRoot "packaging\vocal_process_gui.py"
$DistRoot = Join-Path $ProjectRoot "dist"
$PortableDist = Join-Path $DistRoot "$AppName-portable"
$AppRoot = Join-Path $PortableDist $AppName
$ZipPath = Join-Path $DistRoot "$AppName-portable.zip"
$WorkPath = Join-Path $ProjectRoot "build\pyinstaller"
$SpecPath = Join-Path $ProjectRoot "build"

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

$FfmpegRoot = Join-Path $env:ProgramData "chocolatey\lib\ffmpeg\tools\ffmpeg"
$FfmpegBin = Join-Path $FfmpegRoot "bin"
$FfmpegExe = Join-Path $FfmpegBin "ffmpeg.exe"
$FfprobeExe = Join-Path $FfmpegBin "ffprobe.exe"
$FfmpegLicense = Join-Path $FfmpegRoot "LICENSE"
$FfmpegReadme = Join-Path $FfmpegRoot "README.txt"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment Python not found: $Python"
}

if (-not (Test-Path -LiteralPath $Wrapper)) {
    throw "PyInstaller wrapper not found: $Wrapper"
}

foreach ($Path in @($FfmpegExe, $FfprobeExe, $FfmpegLicense, $FfmpegReadme)) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required FFmpeg file not found: $Path"
    }
}

if (Test-Path -LiteralPath $PortableDist) {
    Assert-ProjectChildPath $PortableDist "Portable output directory"
    Remove-Item -LiteralPath $PortableDist -Recurse -Force
}

if (Test-Path -LiteralPath $ZipPath) {
    Assert-ProjectChildPath $ZipPath "Portable ZIP"
    Remove-Item -LiteralPath $ZipPath -Force
}

$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $PyInstallerOutput = & $Python -m PyInstaller `
        --log-level ERROR `
        --noconfirm `
        --clean `
        --windowed `
        --name $AppName `
        --distpath $PortableDist `
        --workpath $WorkPath `
        --specpath $SpecPath `
        $Wrapper 2>&1
    $PyInstallerExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
}

if ($PyInstallerExitCode -ne 0) {
    $PyInstallerOutput | ForEach-Object { Write-Error $_ }
    throw "PyInstaller failed with exit code $PyInstallerExitCode"
}

$BinDir = Join-Path $AppRoot "bin"
$LicensesDir = Join-Path $AppRoot "licenses"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
New-Item -ItemType Directory -Force -Path $LicensesDir | Out-Null

Copy-Item -LiteralPath $FfmpegExe -Destination (Join-Path $BinDir "ffmpeg.exe") -Force
Copy-Item -LiteralPath $FfprobeExe -Destination (Join-Path $BinDir "ffprobe.exe") -Force
Copy-Item -LiteralPath $FfmpegLicense -Destination (Join-Path $LicensesDir "FFmpeg-LICENSE.txt") -Force
Copy-Item -LiteralPath $FfmpegReadme -Destination (Join-Path $LicensesDir "FFmpeg-README.txt") -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\README_PORTABLE.txt") -Destination (Join-Path $AppRoot "README_PORTABLE.txt") -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\THIRD_PARTY_NOTICES.txt") -Destination (Join-Path $AppRoot "THIRD_PARTY_NOTICES.txt") -Force

$BuildTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
$GitBranch = (& git -C $ProjectRoot rev-parse --abbrev-ref HEAD 2>$null)
$GitCommit = (& git -C $ProjectRoot rev-parse --short HEAD 2>$null)
$GitStatus = (& git -C $ProjectRoot status --short 2>$null)
$SourceState = if ($GitStatus) { "working tree included uncommitted changes" } else { "working tree clean" }

@(
    "VocalProcess portable build"
    "Build time: $BuildTime"
    "Git branch: $GitBranch"
    "Git commit: $GitCommit"
    "Source state: $SourceState"
) | Set-Content -LiteralPath (Join-Path $AppRoot "BUILD_INFO.txt") -Encoding UTF8

Compress-Archive -LiteralPath $AppRoot -DestinationPath $ZipPath -Force

Write-Host "Portable package created:"
Write-Host $ZipPath
