param(
    [string]$AppName = "VocalProcess",
    [string]$ZipPath = "",
    [string]$PortableRoot = "",
    [string]$ExtractRoot = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $ExtractRoot) {
    $ExtractRoot = Join-Path $ProjectRoot ".tmp\portable-runtime-check"
}

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

if ($ZipPath) {
    if (-not (Test-Path -LiteralPath $ZipPath)) {
        throw "Portable ZIP not found: $ZipPath"
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Zip = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $ZipPath).Path)
    try {
        $Entries = @{}
        foreach ($Entry in $Zip.Entries) {
            $Entries[$Entry.FullName.Replace("/", "\")] = $true
        }
        $RequiredZipPaths = @(
            "$AppName\$AppName.exe",
            "$AppName\bin\ffmpeg.exe",
            "$AppName\bin\ffprobe.exe",
            "$AppName\_internal\torch\_C.cp311-win_amd64.pyd",
            "$AppName\_internal\torch\lib\c10.dll",
            "$AppName\_internal\torch\lib\torch.dll",
            "$AppName\_internal\torch\lib\torch_cpu.dll",
            "$AppName\_internal\torch\lib\torch_python.dll",
            "$AppName\_internal\torchaudio\__init__.py",
            "$AppName\_internal\whisper\__init__.py",
            "$AppName\_internal\faster_whisper\__init__.py"
        )
        $MissingZipEntries = @()
        foreach ($RelativePath in $RequiredZipPaths) {
            if (-not $Entries.ContainsKey($RelativePath)) {
                $MissingZipEntries += $RelativePath
            }
        }
        foreach ($DirectoryPath in @("$AppName\models\", "$AppName\uvr-worker\")) {
            $FoundDirectoryEntry = $false
            foreach ($EntryName in $Entries.Keys) {
                if ($EntryName.StartsWith($DirectoryPath, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $FoundDirectoryEntry = $true
                    break
                }
            }
            if (-not $FoundDirectoryEntry) {
                $MissingZipEntries += $DirectoryPath
            }
        }
        if ($MissingZipEntries.Count -gt 0) {
            throw "Portable ZIP runtime is incomplete. Missing: $($MissingZipEntries -join ', ')"
        }
    }
    finally {
        $Zip.Dispose()
    }
    Write-Host "Portable ZIP runtime check passed:"
    Write-Host $ZipPath
    return
}

if (-not $PortableRoot) {
    $PortableRoot = Join-Path $ProjectRoot "dist\$AppName-portable\$AppName"
}

$AppRoot = (Resolve-Path -LiteralPath $PortableRoot).Path
$RequiredPaths = @(
    "$AppName.exe",
    "bin\ffmpeg.exe",
    "bin\ffprobe.exe",
    "_internal\torch\_C.cp311-win_amd64.pyd",
    "_internal\torch\lib\c10.dll",
    "_internal\torch\lib\torch.dll",
    "_internal\torch\lib\torch_cpu.dll",
    "_internal\torch\lib\torch_python.dll",
    "_internal\torchaudio\__init__.py",
    "_internal\whisper\__init__.py",
    "_internal\faster_whisper\__init__.py",
    "models",
    "uvr-worker"
)

$Missing = @()
foreach ($RelativePath in $RequiredPaths) {
    $Path = Join-Path $AppRoot $RelativePath
    if (-not (Test-Path -LiteralPath $Path)) {
        $Missing += $RelativePath
    }
}

if ($Missing.Count -gt 0) {
    throw "Portable runtime is incomplete. Missing: $($Missing -join ', ')"
}

Write-Host "Portable runtime check passed:"
Write-Host $AppRoot
