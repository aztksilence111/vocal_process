[CmdletBinding()]
param(
    [string]$Root = "tests_real",
    [string]$Manifest,
    [switch]$Render,
    [string]$ComputeDevice = "auto",
    [string]$SourceSeparation = "never",
    [int]$MaxCases,
    [string]$Case,
    [string]$Split,
    [string]$OutputRoot,
    [string]$WriteTemplate
)

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot "..\\.venv311\\Scripts\\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

$args = @("-m", "audio_processor.real_eval", "--root", $Root, "--compute-device", $ComputeDevice, "--source-separation", $SourceSeparation)
if ($Manifest) { $args += @("--manifest", $Manifest) }
if ($Render) { $args += "--render" }
if ($MaxCases) { $args += @("--max-cases", "$MaxCases") }
if ($Case) { $args += @("--case", $Case) }
if ($Split) { $args += @("--split", $Split) }
if ($OutputRoot) { $args += @("--output-root", $OutputRoot) }
if ($WriteTemplate) { $args += @("--write-template", $WriteTemplate) }

& $python @args
