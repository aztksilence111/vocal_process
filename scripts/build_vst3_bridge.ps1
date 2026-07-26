param(
    [string]$JuceRoot = "",
    [string]$Configuration = "Release",
    [string]$Generator = "Visual Studio 18 2026",
    [string]$Platform = "x64"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SourceDir = Join-Path $ProjectRoot "native\vst3_bridge"
$BuildDir = Join-Path $ProjectRoot "build\vst3_bridge"

if (-not $JuceRoot) {
    $JuceRoot = Join-Path $ProjectRoot "extern\JUCE"
}

function Resolve-CMake {
    $VsCMake = Join-Path $env:ProgramFiles "Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
    if (Test-Path -LiteralPath $VsCMake) {
        return $VsCMake
    }

    $Command = Get-Command cmake -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }

    throw "CMake was not found. Install CMake or Visual Studio CMake tools."
}

function Resolve-MSVCCompiler {
    $Cache = Join-Path $BuildDir "CMakeCache.txt"
    if (Test-Path -LiteralPath $Cache) {
        $Cached = Select-String -LiteralPath $Cache -Pattern '^CMAKE_CXX_COMPILER:FILEPATH=(.+)$' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($Cached -and (Test-Path -LiteralPath $Cached.Matches[0].Groups[1].Value)) {
            return $Cached.Matches[0].Groups[1].Value
        }
    }

    $ToolsRoot = Join-Path $env:ProgramFiles "Microsoft Visual Studio\18\Community\VC\Tools\MSVC"
    if (Test-Path -LiteralPath $ToolsRoot) {
        $Compiler = Get-ChildItem -LiteralPath $ToolsRoot -Directory |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "bin\Hostx64\x64\cl.exe" } |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1

        if ($Compiler) {
            return $Compiler
        }
    }

    return ""
}

function Join-ProcessArguments {
    param([string[]]$Arguments)

    $Quoted = foreach ($Argument in $Arguments) {
        if ($Argument -notmatch '[\s"]') {
            $Argument
        } else {
            '"' + $Argument.Replace('"', '\"') + '"'
        }
    }

    return ($Quoted -join " ")
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

    $PathValue = [System.Environment]::GetEnvironmentVariable("Path", "Process")
    if (-not $PathValue) {
        $PathValue = [System.Environment]::GetEnvironmentVariable("PATH", "Process")
    }

    $Command = 'set "PATH=" && set "Path=' + $PathValue + '" && ' + (Quote-CmdArgument -Argument $FilePath)
    $JoinedArguments = Join-ProcessArguments -Arguments $Arguments
    if ($JoinedArguments) {
        $Command += " " + $JoinedArguments
    }

    & $env:ComSpec /d /c $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $JuceRoot "CMakeLists.txt"))) {
    throw "JUCE checkout not found at $JuceRoot. Clone JUCE there or pass -JuceRoot."
}

$CMake = Resolve-CMake
$Compiler = Resolve-MSVCCompiler

$ConfigureArgs = @(
    "-S", $SourceDir,
    "-B", $BuildDir,
    "-G", $Generator,
    "-A", $Platform,
    "-DJUCE_ROOT=$JuceRoot"
)

if ($Compiler) {
    $CompilerForCMake = $Compiler.Replace("\", "/")
    $ConfigureArgs += "-DCMAKE_C_COMPILER=$CompilerForCMake"
    $ConfigureArgs += "-DCMAKE_CXX_COMPILER=$CompilerForCMake"
}

Invoke-CheckedProcess -FilePath $CMake -Arguments $ConfigureArgs -FailureMessage "CMake configure failed"
Invoke-CheckedProcess -FilePath $CMake -Arguments @("--build", $BuildDir, "--config", $Configuration, "--target", "VocalProcessBridge_VST3") -FailureMessage "VST3 build failed"

$Vst3Path = Join-Path $BuildDir "VocalProcessBridge_artefacts\$Configuration\VST3\VocalProcess Bridge.vst3"
Write-Host "VST3 bridge created:"
Write-Host $Vst3Path
