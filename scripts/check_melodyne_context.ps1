param(
    [string[]]$DriveRoots
)

$ErrorActionPreference = "Stop"

if (-not $DriveRoots -or $DriveRoots.Count -eq 0) {
    $DriveRoots = Get-PSDrive -PSProvider FileSystem |
        Where-Object { $_.Root -match '^[A-Z]:\\$' } |
        ForEach-Object { $_.Root.TrimEnd('\') }
}

function Add-ExistingPath {
    param(
        [System.Collections.Generic.List[string]]$List,
        [string]$Path
    )

    if ($Path -and (Test-Path -LiteralPath $Path)) {
        $resolved = (Resolve-Path -LiteralPath $Path).Path
        if (-not $List.Contains($resolved)) {
            $List.Add($resolved)
        }
    }
}

$standalonePaths = [System.Collections.Generic.List[string]]::new()
$vst3Paths = [System.Collections.Generic.List[string]]::new()
$legacyPaths = [System.Collections.Generic.List[string]]::new()

foreach ($root in $DriveRoots) {
    Add-ExistingPath $standalonePaths (Join-Path $root "Program Files\Celemony")
    Add-ExistingPath $vst3Paths (Join-Path $root "Program Files\Common Files\VST3\Celemony")
    Add-ExistingPath $vst3Paths (Join-Path $root "Program Files\Common Files\VST3\Melodyne.vst3")

    Add-ExistingPath $legacyPaths (Join-Path $root "Program Files (x86)\Celemony")
    Add-ExistingPath $legacyPaths (Join-Path $root "Program Files (x86)\Common Files\VST3\Celemony")
    Add-ExistingPath $legacyPaths (Join-Path $root "Program Files (x86)\Common Files\VST3\Melodyne.vst3")
}

Write-Host "Melodyne / Celemony context"
Write-Host ""

if ($standalonePaths.Count -gt 0 -or $vst3Paths.Count -gt 0) {
    Write-Host "Standard 64-bit locations detected:"
    foreach ($path in ($standalonePaths + $vst3Paths)) {
        Write-Host "  $path"
    }
} else {
    Write-Host "No 64-bit Melodyne/Celemony installation was found in standard locations."
}

if ($legacyPaths.Count -gt 0) {
    Write-Host ""
    Write-Host "Legacy 32-bit locations detected:"
    foreach ($path in $legacyPaths) {
        Write-Host "  $path"
    }
}

Write-Host ""
Write-Host "Interpretation:"
Write-Host "- VocalProcess Bridge is a 64-bit VST3 DAW control plug-in."
Write-Host "- Melodyne is primarily an editor/ARA plug-in workflow target, not a generic host test target."
Write-Host "- For Melodyne workflows, export PCM WAV or the DAW timeline project, then use Melodyne inside a DAW that can load both plug-ins."

if ($standalonePaths.Count -eq 0 -and $vst3Paths.Count -eq 0 -and $legacyPaths.Count -gt 0) {
    Write-Host "- This machine currently has only legacy 32-bit Melodyne/Celemony paths, so 64-bit bridge host validation cannot be claimed here."
}
