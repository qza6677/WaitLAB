param(
    [string]$OutputDirectory = '',
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = (Get-Command python -ErrorAction Stop).Source
$ReleaseDir = if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    Join-Path $ProjectRoot 'release'
} elseif ([IO.Path]::IsPathRooted($OutputDirectory)) {
    [IO.Path]::GetFullPath($OutputDirectory)
} else {
    [IO.Path]::GetFullPath((Join-Path $ProjectRoot $OutputDirectory))
}
$BuildDir = Join-Path $ProjectRoot 'build\pyinstaller'
$Icon = Join-Path $ProjectRoot 'packaging\waitlab.ico'
$VersionFile = Join-Path $ProjectRoot 'packaging\version_info.txt'
$CookieAssets = Join-Path $ProjectRoot 'resources\Cookie\processed\sprites-96'
$CookieAssetsHighRes = Join-Path $ProjectRoot 'resources\Cookie\processed\sprites-256'
$EntryPoint = Join-Path $ProjectRoot 'run_waitlab.py'
$ProjectVersion = (
    Get-Content (Join-Path $ProjectRoot 'pyproject.toml') |
        Where-Object { $_ -match '^version\s*=' } |
        Select-Object -First 1
) -replace '.*"([^"]+)".*', '$1'
$VersionParts = $ProjectVersion.Split('.')
if ($VersionParts.Count -ne 3 -or ($VersionParts | Where-Object { $_ -notmatch '^\d+$' })) {
    throw "pyproject.toml version must use MAJOR.MINOR.PATCH: $ProjectVersion"
}
$Portable = Join-Path $ReleaseDir 'WaitLAB.exe'
$ChecksumManifest = Join-Path $ReleaseDir 'SHA256SUMS.txt'
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
# Never let a previous local build leak into a release archive.  Keep any
# versioned test subdirectories intact, but remove only root-level artifacts
# produced by this script.
Remove-Item -LiteralPath $Portable, $ChecksumManifest -Force -ErrorAction SilentlyContinue
Get-ChildItem -LiteralPath $ReleaseDir -Filter 'WaitLAB-Setup-*.exe' -File -ErrorAction SilentlyContinue |
    Remove-Item -Force
$GeneratedVersionFile = Join-Path $BuildDir 'version_info.generated.txt'
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
$VersionInfo = Get-Content -LiteralPath $VersionFile -Raw
$VersionInfo = $VersionInfo.Replace('__VERSION__', $ProjectVersion)
$VersionInfo = $VersionInfo.Replace('__VERSION_MAJOR__', $VersionParts[0])
$VersionInfo = $VersionInfo.Replace('__VERSION_MINOR__', $VersionParts[1])
$VersionInfo = $VersionInfo.Replace('__VERSION_PATCH__', $VersionParts[2])
Set-Content -LiteralPath $GeneratedVersionFile -Value $VersionInfo -Encoding utf8

Push-Location $ProjectRoot
try {
    & $Python (Join-Path $ProjectRoot 'scripts\export_icon.py')
    if ($LASTEXITCODE -ne 0) { throw 'Icon export failed.' }

    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name WaitLAB `
        --icon $Icon `
        --version-file $GeneratedVersionFile `
        --add-data "$CookieAssets;resources/Cookie/processed/sprites-96" `
        --add-data "$CookieAssetsHighRes;resources/Cookie/processed/sprites-256" `
        --distpath $ReleaseDir `
        --workpath $BuildDir `
        --specpath (Join-Path $ProjectRoot 'build') `
        $EntryPoint
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    if (-not (Test-Path -LiteralPath $Portable)) { throw 'Portable executable was not produced.' }
    $fileVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($Portable).FileVersion
    if ($fileVersion -notmatch "^$([regex]::Escape($ProjectVersion))(\.0)?$") {
        throw "Portable executable version mismatch: expected $ProjectVersion, got $fileVersion"
    }

    if (-not $SkipInstaller) {
        $IsccCandidates = @(
            (Join-Path ${env:LOCALAPPDATA} 'Programs\Inno Setup 6\ISCC.exe'),
            (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
            (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
        )
        $Iscc = $IsccCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
        if ($Iscc) {
            try {
                # Pass the semver from pyproject.toml explicitly. Inno Setup's
                # GetFileVersion exposes Windows' four-component form (0.5.12.0),
                # while GitHub assets intentionally use the three-component tag.
                $InnoSourceDir = $ReleaseDir.Replace('\', '/')
                & $Iscc "/DMyAppVersion=$ProjectVersion" "/DMyAppSourceDir=$InnoSourceDir" "/DMyAppOutputDir=$InnoSourceDir" (Join-Path $ProjectRoot 'packaging\WaitLAB.iss')
                if ($LASTEXITCODE -ne 0) { throw 'Inno Setup build failed.' }
            } catch {
                Write-Warning ("Inno Setup build was skipped: {0}" -f $_.Exception.Message)
            }
        } else {
            Write-Warning 'Inno Setup 6 was not found. The portable WaitLAB.exe was built; installer build was skipped.'
        }
    } else {
        Write-Host 'Installer build skipped by request.'
    }

    $Installer = Join-Path $ReleaseDir "WaitLAB-Setup-$ProjectVersion.exe"
    $HashTargets = @($Portable)
    if (Test-Path -LiteralPath $Installer) {
        $HashTargets += $Installer
    }
    $Hashes = Get-FileHash $HashTargets -Algorithm SHA256
    $HashLines = $Hashes | ForEach-Object { '{0} *{1}' -f $_.Hash, (Split-Path -Leaf $_.Path) }
    Set-Content -LiteralPath $ChecksumManifest -Value $HashLines -Encoding ascii
    $Hashes
} finally {
    Pop-Location
}
