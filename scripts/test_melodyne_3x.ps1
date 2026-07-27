param(
    [string]$MelodyneExe = "E:\Program Files (x86)\Celemony\Melodyne.3.2\Melodyne.exe",
    [int]$WaitSeconds = 12,
    [switch]$Hidden
)

$ErrorActionPreference = "Stop"

function Resolve-MelodyneExe {
    param([string]$PreferredPath)

    $Candidates = @(
        $PreferredPath,
        "E:\Program Files (x86)\Celemony\Melodyne.3.2\Melodyne.exe",
        "D:\Program Files (x86)\Celemony\Melodyne.3.2\Melodyne.exe",
        "E:\Program Files (x86)\Celemony\Melodyne.3.0\Melodyne.exe",
        "D:\Program Files (x86)\Celemony\Melodyne.3.0\Melodyne.exe"
    ) | Where-Object { $_ } | Select-Object -Unique

    foreach ($Path in $Candidates) {
        if (Test-Path -LiteralPath $Path) {
            return (Resolve-Path -LiteralPath $Path).Path
        }
    }

    throw "Melodyne 3.x executable not found. Checked: $($Candidates -join '; ')"
}

$ResolvedExe = Resolve-MelodyneExe -PreferredPath $MelodyneExe
$ExeInfo = Get-Item -LiteralPath $ResolvedExe

Write-Host "Melodyne 3.x target:"
Write-Host $ResolvedExe
Write-Host ("Version: {0}" -f $ExeInfo.VersionInfo.FileVersion)

$StartParams = @{
    FilePath = $ResolvedExe
    PassThru = $true
}
if ($Hidden) {
    $StartParams.WindowStyle = "Hidden"
}

$Process = Start-Process @StartParams
Start-Sleep -Seconds $WaitSeconds

if ($Process.HasExited) {
    throw "Melodyne 3.x exited during launch test with code $($Process.ExitCode)"
}

[void]$Process.CloseMainWindow()
Start-Sleep -Seconds 3
if (-not $Process.HasExited) {
    Stop-Process -Id $Process.Id -Force
}

Write-Host "Melodyne 3.x launch test passed."
