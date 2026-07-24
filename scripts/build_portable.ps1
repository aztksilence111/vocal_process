param(
    [string]$AppName = "VocalProcess",
    [switch]$SkipModelRuntime,
    [switch]$AnalyzeModelRuntime,
    [string]$ModelCacheRoot = ""
)

$ErrorActionPreference = "Stop"
$BundleModelRuntime = -not $SkipModelRuntime.IsPresent

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SitePackages = Join-Path $ProjectRoot ".venv\Lib\site-packages"
$Wrapper = Join-Path $ProjectRoot "packaging\vocal_process_gui.py"
$DistRoot = Join-Path $ProjectRoot "dist"
$PortableDist = Join-Path $DistRoot "$AppName-portable"
$AppRoot = Join-Path $PortableDist $AppName
$ZipPath = Join-Path $DistRoot "$AppName-portable.zip"
$WorkPath = Join-Path $ProjectRoot "build\pyinstaller"
$SpecPath = Join-Path $ProjectRoot "build"
if (-not $ModelCacheRoot) {
    $ModelCacheRoot = Join-Path $ProjectRoot ".tmp\model-cache"
}

$ModelRuntimeCollectAll = @(
    "torch",
    "torchaudio",
    "whisper",
    "demucs",
    "speechbrain",
    "tiktoken",
    "numba",
    "llvmlite",
    "numpy",
    "scipy",
    "soundfile",
    "yaml",
    "huggingface_hub",
    "hyperpyyaml",
    "sentencepiece",
    "julius",
    "lameenc"
)

$ModelRuntimeCollectSubmodules = @("tiktoken_ext")

$ModelRuntimeHiddenImports = @(
    "torch",
    "torchaudio",
    "whisper",
    "demucs.separate",
    "demucs.pretrained",
    "speechbrain.inference.speaker",
    "speechbrain.dataio.dataio"
)

$ModelRuntimeMetadata = @(
    "torch",
    "torchaudio",
    "openai-whisper",
    "demucs",
    "speechbrain",
    "tiktoken",
    "numba",
    "llvmlite",
    "numpy",
    "scipy",
    "soundfile",
    "PyYAML",
    "huggingface-hub",
    "HyperPyYAML",
    "sentencepiece",
    "julius",
    "lameenc"
)

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

function Test-PythonModule {
    param([string]$ModuleName)

    $Code = "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$ModuleName') is not None else 1)"
    & $Python -c $Code *> $null
    return $LASTEXITCODE -eq 0
}

function Test-PythonDistribution {
    param([string]$DistributionName)

    $Code = "import importlib.metadata as md, sys`ntry:`n    md.distribution('$DistributionName')`nexcept Exception:`n    sys.exit(1)`nelse:`n    sys.exit(0)"
    & $Python -c $Code *> $null
    return $LASTEXITCODE -eq 0
}

function Copy-DirectoryWithRobocopy {
    param(
        [string]$Source,
        [string]$Destination,
        [string]$Label
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        throw "$Label source not found: $Source"
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & robocopy $Source $Destination /E /R:1 /W:1 /NFL /NDL /NJH /NJS | Out-Null
    $RobocopyExitCode = $LASTEXITCODE
    if ($RobocopyExitCode -gt 7) {
        throw "$Label copy failed with robocopy exit code $RobocopyExitCode"
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

if ($BundleModelRuntime -and (-not (Test-Path -LiteralPath $SitePackages))) {
    throw "Virtual environment site-packages not found: $SitePackages"
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
    $PyInstallerArgs = @(
        "--log-level", "ERROR",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name", $AppName,
        "--distpath", $PortableDist,
        "--workpath", $WorkPath,
        "--specpath", $SpecPath
    )

    if ($BundleModelRuntime -and $AnalyzeModelRuntime) {
        foreach ($Package in $ModelRuntimeCollectAll) {
            if (Test-PythonModule $Package) {
                $PyInstallerArgs += @("--collect-all", $Package)
            }
        }
        foreach ($Package in $ModelRuntimeCollectSubmodules) {
            if (Test-PythonModule $Package) {
                $PyInstallerArgs += @("--collect-submodules", $Package)
            }
        }
        foreach ($Module in $ModelRuntimeHiddenImports) {
            if (Test-PythonModule $Module) {
                $PyInstallerArgs += @("--hidden-import", $Module)
            }
        }
        foreach ($Distribution in $ModelRuntimeMetadata) {
            if (Test-PythonDistribution $Distribution) {
                $PyInstallerArgs += @("--copy-metadata", $Distribution)
            }
        }
    }

    $PyInstallerArgs += $Wrapper
    $PyInstallerOutput = & $Python -m PyInstaller @PyInstallerArgs 2>&1
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
$PortableModelRoot = Join-Path $AppRoot "models"
$InternalDir = Join-Path $AppRoot "_internal"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
New-Item -ItemType Directory -Force -Path $LicensesDir | Out-Null

Copy-Item -LiteralPath $FfmpegExe -Destination (Join-Path $BinDir "ffmpeg.exe") -Force
Copy-Item -LiteralPath $FfprobeExe -Destination (Join-Path $BinDir "ffprobe.exe") -Force
Copy-Item -LiteralPath $FfmpegLicense -Destination (Join-Path $LicensesDir "FFmpeg-LICENSE.txt") -Force
Copy-Item -LiteralPath $FfmpegReadme -Destination (Join-Path $LicensesDir "FFmpeg-README.txt") -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\README_PORTABLE.txt") -Destination (Join-Path $AppRoot "README_PORTABLE.txt") -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\THIRD_PARTY_NOTICES.txt") -Destination (Join-Path $AppRoot "THIRD_PARTY_NOTICES.txt") -Force

$BundledSitePackages = $false
if ($BundleModelRuntime) {
    if (-not (Test-Path -LiteralPath $InternalDir)) {
        throw "PyInstaller internal directory not found: $InternalDir"
    }
    Copy-DirectoryWithRobocopy $SitePackages $InternalDir "Python site-packages"
    $BundledSitePackages = $true
}

$BundledModelCache = $false
$ResolvedModelCache = ""
if ($BundleModelRuntime -and (Test-Path -LiteralPath $ModelCacheRoot)) {
    $ResolvedModelCache = (Resolve-Path -LiteralPath $ModelCacheRoot).Path
    Copy-DirectoryWithRobocopy $ResolvedModelCache $PortableModelRoot "Model cache"
    $BundledModelCache = $true
}

$BuildTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
$GitBranch = (& git -C $ProjectRoot rev-parse --abbrev-ref HEAD 2>$null)
$GitCommit = (& git -C $ProjectRoot rev-parse --short HEAD 2>$null)
$GitStatus = (& git -C $ProjectRoot status --short 2>$null)
$SourceState = if ($GitStatus) { "working tree included uncommitted changes" } else { "working tree clean" }
$RuntimeState = if ($BundledSitePackages) {
    "local pretrained runtime dependencies copied from $SitePackages"
} elseif ($BundleModelRuntime -and $AnalyzeModelRuntime) {
    "local pretrained runtime dependencies collected by PyInstaller"
} elseif ($BundleModelRuntime) {
    "model runtime requested but no site-packages copied"
} else {
    "model runtime collection disabled"
}
$ModelCacheState = if ($BundledModelCache) { "bundled from $ResolvedModelCache" } else { "not bundled" }

@(
    "VocalProcess portable build"
    "Build time: $BuildTime"
    "Git branch: $GitBranch"
    "Git commit: $GitCommit"
    "Source state: $SourceState"
    "Model runtime: $RuntimeState"
    "Model cache: $ModelCacheState"
) | Set-Content -LiteralPath (Join-Path $AppRoot "BUILD_INFO.txt") -Encoding UTF8

Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $PortableDist,
    $ZipPath,
    [System.IO.Compression.CompressionLevel]::Fastest,
    $false
)

Write-Host "Portable package created:"
Write-Host $ZipPath
