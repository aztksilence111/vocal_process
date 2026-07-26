param(
    [string]$PluginPath = "build\vst3_bridge\VocalProcessBridge_artefacts\Release\VST3\VocalProcess Bridge.vst3",
    [string]$JuceRoot = "extern\JUCE",
    [string]$BuildDir = "build\vst3_probe",
    [string]$Configuration = "Release",
    [string]$Generator = "Visual Studio 18 2026",
    [string]$Platform = "x64"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

$resolvedPlugin = Resolve-Path -LiteralPath $PluginPath
$resolvedJuce = Resolve-Path -LiteralPath $JuceRoot
$resolvedBuildDir = [System.IO.Path]::GetFullPath((Join-Path $root $BuildDir))
$resolvedBuildRoot = [System.IO.Path]::GetFullPath((Join-Path $root "build"))

if (-not $resolvedBuildDir.StartsWith($resolvedBuildRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a probe build directory outside $resolvedBuildRoot"
}

function Resolve-CMake {
    $vsCMake = Join-Path $env:ProgramFiles "Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
    if (Test-Path -LiteralPath $vsCMake) {
        return $vsCMake
    }

    $command = Get-Command cmake -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    throw "CMake was not found. Install CMake or Visual Studio CMake tools."
}

function Resolve-MSVCCompiler {
    $bridgeCache = Join-Path $root "build\vst3_bridge\CMakeCache.txt"
    if (Test-Path -LiteralPath $bridgeCache) {
        $cached = Select-String -LiteralPath $bridgeCache -Pattern '^CMAKE_CXX_COMPILER:FILEPATH=(.+)$' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cached -and (Test-Path -LiteralPath $cached.Matches[0].Groups[1].Value)) {
            return $cached.Matches[0].Groups[1].Value
        }
    }

    $toolsRoot = Join-Path $env:ProgramFiles "Microsoft Visual Studio\18\Community\VC\Tools\MSVC"
    if (Test-Path -LiteralPath $toolsRoot) {
        $compiler = Get-ChildItem -LiteralPath $toolsRoot -Directory |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "bin\Hostx64\x64\cl.exe" } |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1

        if ($compiler) {
            return $compiler
        }
    }

    return ""
}

function Join-ProcessArguments {
    param([string[]]$Arguments)

    $quoted = foreach ($argument in $Arguments) {
        if ($argument -notmatch '[\s"]') {
            $argument
        } else {
            '"' + $argument.Replace('"', '\"') + '"'
        }
    }

    return ($quoted -join " ")
}

function Quote-CmdArgument {
    param([string]$Argument)

    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }

    return '"' + $Argument.Replace('"', '\"') + '"'
}

function Invoke-CheckedProcess {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$FailureMessage
    )

    $pathValue = [System.Environment]::GetEnvironmentVariable("Path", "Process")
    if (-not $pathValue) {
        $pathValue = [System.Environment]::GetEnvironmentVariable("PATH", "Process")
    }

    $command = 'set "PATH=" && set "Path=' + $pathValue + '" && ' + (Quote-CmdArgument -Argument $FilePath)
    $joinedArguments = Join-ProcessArguments -Arguments $Arguments
    if ($joinedArguments) {
        $command += " " + $joinedArguments
    }

    & $env:ComSpec /d /c $command
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage with exit code $LASTEXITCODE"
    }
}

$cmake = Resolve-CMake
$compiler = Resolve-MSVCCompiler

$cache = Join-Path $resolvedBuildDir "CMakeCache.txt"
if (Test-Path -LiteralPath $cache) {
    $cachedCompiler = Select-String -LiteralPath $cache -Pattern '^CMAKE_CXX_COMPILER:FILEPATH=(.+)$' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ((-not $cachedCompiler) -or (-not (Test-Path -LiteralPath $cachedCompiler.Matches[0].Groups[1].Value))) {
        Remove-Item -LiteralPath $resolvedBuildDir -Recurse -Force
    }
}

$configureArgs = @(
    "-S", "native\vst3_probe",
    "-B", $resolvedBuildDir,
    "-G", $Generator,
    "-A", $Platform,
    "-DJUCE_ROOT=$resolvedJuce"
)

if ($compiler) {
    $compilerForCMake = $compiler.Replace("\", "/")
    $configureArgs += "-DCMAKE_C_COMPILER=$compilerForCMake"
    $configureArgs += "-DCMAKE_CXX_COMPILER=$compilerForCMake"
}

Invoke-CheckedProcess -FilePath $cmake -Arguments $configureArgs -FailureMessage "CMake configure failed"

Invoke-CheckedProcess -FilePath $cmake -Arguments @("--build", $resolvedBuildDir, "--config", $Configuration, "--target", "VocalProcessVst3Probe") -FailureMessage "Probe build failed"

$probe = Join-Path $resolvedBuildDir "$Configuration\VocalProcessVst3Probe.exe"
if (-not (Test-Path -LiteralPath $probe)) {
    throw "Probe executable not found: $probe"
}

Invoke-CheckedProcess -FilePath $probe -Arguments @([string]$resolvedPlugin) -FailureMessage "VST3 probe failed"
