param(
    [string]$AppName = "VocalProcess",
    [switch]$SkipModelRuntime,
    [switch]$AnalyzeModelRuntime,
    [switch]$IncludeVst3Bridge,
    [string]$ModelCacheRoot = "",
    [string]$Python = "",
    [string]$PackageSuffix = "",
    [switch]$IncludeUvrWorker
)

$ErrorActionPreference = "Stop"
$BundleModelRuntime = -not $SkipModelRuntime.IsPresent

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Wrapper = Join-Path $ProjectRoot "packaging\vocal_process_gui.py"
$DistRoot = Join-Path $ProjectRoot "dist"
$NormalizedPackageSuffix = $PackageSuffix.Trim().Trim("-")
if ($NormalizedPackageSuffix) {
    $PackageName = if ($IncludeVst3Bridge.IsPresent) {
        "$AppName-portable-$NormalizedPackageSuffix-vst3"
    } else {
        "$AppName-portable-$NormalizedPackageSuffix"
    }
} else {
    $PackageName = if ($IncludeVst3Bridge.IsPresent) { "$AppName-portable-vst3" } else { "$AppName-portable" }
}
$PortableDist = Join-Path $DistRoot $PackageName
$AppRoot = Join-Path $PortableDist $AppName
$ZipPath = Join-Path $DistRoot "$PackageName.zip"
$WorkPath = Join-Path $ProjectRoot "build\pyinstaller"
$SpecPath = Join-Path $ProjectRoot "build"
if (-not $ModelCacheRoot) {
    $ModelCacheRoot = Join-Path $ProjectRoot ".tmp\model-cache"
}
$UvrWorkerRoot = Join-Path $ProjectRoot ".uvr-worker"

function Resolve-MainPython {
    if ($Python) {
        if (-not (Test-Path -LiteralPath $Python)) {
            throw "Python executable not found: $Python"
        }
        return (Resolve-Path -LiteralPath $Python).Path
    }

    $Candidates = @(
        $env:VOCAL_PROCESS_PYTHON,
        (Join-Path $ProjectRoot ".venv311\Scripts\python.exe"),
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
    )

    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }

    throw "Main runtime Python not found. Run scripts\bootstrap_py311_env.ps1 first, or pass -Python."
}

$Python = Resolve-MainPython
$VenvRoot = Split-Path -Parent (Split-Path -Parent $Python)
$SitePackages = Join-Path $VenvRoot "Lib\site-packages"

$ModelRuntimeCollectAll = @(
    "torch",
    "torchaudio",
    "whisper",
    "faster_whisper",
    "ctranslate2",
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
    "lameenc",
    "tokenizers",
    "transformers",
    "whisperx",
    "pyannote",
    "torchmetrics",
    "lightning",
    "pytorch_lightning"
)

$ModelRuntimeCollectSubmodules = @("tiktoken_ext")

$ModelRuntimeHiddenImports = @(
    "torch",
    "torchaudio",
    "whisper",
    "whisperx",
    "faster_whisper",
    "demucs.separate",
    "demucs.pretrained",
    "speechbrain.inference.speaker",
    "speechbrain.dataio.dataio",
    "pyannote.audio",
    "pyannote.core",
    "pyannote.database",
    "pyannote.metrics",
    "pyannote.pipeline"
)

$ModelRuntimeMetadata = @(
    "torch",
    "torchaudio",
    "openai-whisper",
    "faster-whisper",
    "whisperx",
    "ctranslate2",
    "demucs",
    "speechbrain",
    "pyannote.audio",
    "pyannote.core",
    "pyannote.database",
    "pyannote.metrics",
    "pyannote.pipeline",
    "tiktoken",
    "numba",
    "llvmlite",
    "numpy",
    "scipy",
    "pandas",
    "soundfile",
    "PyYAML",
    "huggingface-hub",
    "transformers",
    "HyperPyYAML",
    "sentencepiece",
    "julius",
    "lameenc",
    "tokenizers",
    "torchvision",
    "torchcodec",
    "torchmetrics",
    "lightning",
    "pytorch-lightning",
    "matplotlib",
    "omegaconf"
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
    throw "Main runtime Python not found: $Python"
}

$PythonVersion = (& $Python -c "import sys; print(sys.version.split()[0])")
if (-not $PythonVersion.StartsWith("3.11.")) {
    throw "Portable build must use Python 3.11.x. Got $PythonVersion from $Python"
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

$BundledVst3Bridge = $false
$Vst3BridgeRoot = Join-Path $ProjectRoot "build\vst3_bridge\VocalProcessBridge_artefacts\Release\VST3\VocalProcess Bridge.vst3"
if ($IncludeVst3Bridge.IsPresent) {
    if (-not (Test-Path -LiteralPath $Vst3BridgeRoot)) {
        throw "VST3 bridge was requested but not found: $Vst3BridgeRoot. Run scripts\build_vst3_bridge.ps1 first."
    }
    $PluginsDir = Join-Path $AppRoot "plugins"
    Copy-DirectoryWithRobocopy $Vst3BridgeRoot (Join-Path $PluginsDir "VocalProcess Bridge.vst3") "VST3 bridge"
    $BundledVst3Bridge = $true
}

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

$BundledUvrWorker = $false
$BundleUvrWorker = $IncludeUvrWorker.IsPresent -or ($BundleModelRuntime -and (Test-Path -LiteralPath $UvrWorkerRoot))
if ($BundleUvrWorker) {
    if (-not (Test-Path -LiteralPath $UvrWorkerRoot)) {
        throw "UVR worker was requested but not found: $UvrWorkerRoot. Run scripts\bootstrap_uvr_worker.ps1 first."
    }
    Copy-DirectoryWithRobocopy $UvrWorkerRoot (Join-Path $AppRoot "uvr-worker") "UVR worker"
    $BundledUvrWorker = $true
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
$UvrWorkerState = if ($BundledUvrWorker) { "bundled from $UvrWorkerRoot" } else { "not bundled" }
$Vst3BridgeState = if ($BundledVst3Bridge) {
    "bundled from $Vst3BridgeRoot"
} elseif ($IncludeVst3Bridge.IsPresent) {
    "requested but not bundled"
} else {
    "not bundled by package flavor"
}

@(
    "VocalProcess portable build"
    "Build time: $BuildTime"
    "Git branch: $GitBranch"
    "Git commit: $GitCommit"
    "Python runtime: $PythonVersion ($Python)"
    "Source state: $SourceState"
    "Model runtime: $RuntimeState"
    "Model cache: $ModelCacheState"
    "UVR worker: $UvrWorkerState"
    "VST3 bridge: $Vst3BridgeState"
) | Set-Content -LiteralPath (Join-Path $AppRoot "BUILD_INFO.txt") -Encoding UTF8

Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $PortableDist,
    $ZipPath,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $false
)

Write-Host "Portable package created:"
Write-Host $ZipPath
