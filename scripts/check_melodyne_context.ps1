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

$melodyne3Paths = [System.Collections.Generic.List[string]]::new()
$melodyne5Paths = [System.Collections.Generic.List[string]]::new()
$vst3Paths = [System.Collections.Generic.List[string]]::new()
$otherCelemonyPaths = [System.Collections.Generic.List[string]]::new()

foreach ($root in $DriveRoots) {
    Add-ExistingPath $melodyne3Paths (Join-Path $root "Program Files\Celemony\Melodyne.3.2")
    Add-ExistingPath $melodyne3Paths (Join-Path $root "Program Files\Celemony\Melodyne.3.0")
    Add-ExistingPath $melodyne3Paths (Join-Path $root "Program Files\Celemony\Melodyne Studio 3.2")
    Add-ExistingPath $melodyne3Paths (Join-Path $root "Program Files\Celemony\Melodyne Studio 3.0")
    Add-ExistingPath $melodyne3Paths (Join-Path $root "Program Files (x86)\Celemony\Melodyne.3.2")
    Add-ExistingPath $melodyne3Paths (Join-Path $root "Program Files (x86)\Celemony\Melodyne.3.0")
    Add-ExistingPath $melodyne3Paths (Join-Path $root "Program Files (x86)\Celemony\Melodyne Studio 3.2")
    Add-ExistingPath $melodyne3Paths (Join-Path $root "Program Files (x86)\Celemony\Melodyne Studio 3.0")

    Add-ExistingPath $melodyne5Paths (Join-Path $root "Program Files\Celemony\Melodyne.5")
    Add-ExistingPath $melodyne5Paths (Join-Path $root "Program Files\Celemony\Melodyne Studio 5")
    Add-ExistingPath $melodyne5Paths (Join-Path $root "Program Files (x86)\Celemony\Melodyne.5")
    Add-ExistingPath $melodyne5Paths (Join-Path $root "Program Files (x86)\Celemony\Melodyne Studio 5")

    Add-ExistingPath $vst3Paths (Join-Path $root "Program Files\Common Files\VST3\Celemony")
    Add-ExistingPath $vst3Paths (Join-Path $root "Program Files\Common Files\VST3\Melodyne.vst3")
    Add-ExistingPath $vst3Paths (Join-Path $root "Program Files (x86)\Common Files\VST3\Celemony")
    Add-ExistingPath $vst3Paths (Join-Path $root "Program Files (x86)\Common Files\VST3\Melodyne.vst3")

    Add-ExistingPath $otherCelemonyPaths (Join-Path $root "Program Files\Celemony")
    Add-ExistingPath $otherCelemonyPaths (Join-Path $root "Program Files (x86)\Celemony")
}

Write-Host "Melodyne / Celemony context"
Write-Host ""

if ($melodyne3Paths.Count -gt 0) {
    Write-Host "Melodyne 3.x compatibility targets detected:"
    foreach ($path in $melodyne3Paths) {
        Write-Host "  $path"
    }
} else {
    Write-Host "No Melodyne 3.x compatibility target was found in standard locations."
}

if ($melodyne5Paths.Count -gt 0) {
    Write-Host ""
    Write-Host "Melodyne 5 compatibility targets detected:"
    foreach ($path in $melodyne5Paths) {
        Write-Host "  $path"
    }
}

if ($vst3Paths.Count -gt 0) {
    Write-Host ""
    Write-Host "Melodyne VST3 locations detected:"
    foreach ($path in $vst3Paths) {
        Write-Host "  $path"
    }
}

if ($otherCelemonyPaths.Count -gt 0) {
    Write-Host ""
    Write-Host "Other Celemony folders detected:"
    foreach ($path in $otherCelemonyPaths) {
        Write-Host "  $path"
    }
}

Write-Host ""
Write-Host "Interpretation:"
Write-Host "- VocalProcess Bridge is a 64-bit VST3 DAW control plug-in."
Write-Host "- Melodyne 3.x is the current compatibility target on this machine."
Write-Host "- For Melodyne workflows, export PCM WAV or the DAW timeline project, then use Melodyne inside a DAW that can load both plug-ins."

if ($melodyne3Paths.Count -eq 0 -and $melodyne5Paths.Count -eq 0 -and $vst3Paths.Count -eq 0 -and $otherCelemonyPaths.Count -gt 0) {
    Write-Host "- This machine currently has only generic Celemony folders, so Melodyne 3.x compatibility cannot be claimed here."
}
