$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = (Get-Command python -ErrorAction Stop).Source
$ReleaseDir = Join-Path $ProjectRoot 'release'
$BuildDir = Join-Path $ProjectRoot 'build\pyinstaller'
$Icon = Join-Path $ProjectRoot 'packaging\waitlab.ico'
$VersionFile = Join-Path $ProjectRoot 'packaging\version_info.txt'
$CookieAssets = Join-Path $ProjectRoot 'resources\Cookie\processed\sprites-96'
$EntryPoint = Join-Path $ProjectRoot 'run_waitlab.py'
$ProjectVersion = (
    Get-Content (Join-Path $ProjectRoot 'pyproject.toml') |
        Where-Object { $_ -match '^version\s*=' } |
        Select-Object -First 1
) -replace '.*"([^"]+)".*', '$1'

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
        --version-file $VersionFile `
        --add-data "$CookieAssets;resources/Cookie/processed/sprites-96" `
        --distpath $ReleaseDir `
        --workpath $BuildDir `
        --specpath (Join-Path $ProjectRoot 'build') `
        $EntryPoint
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

    $IsccCandidates = @(
        (Join-Path ${env:LOCALAPPDATA} 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    $Iscc = $IsccCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if ($Iscc) {
        try {
            & $Iscc (Join-Path $ProjectRoot 'packaging\WaitLAB.iss')
            if ($LASTEXITCODE -ne 0) { throw 'Inno Setup build failed.' }
        } catch {
            Write-Warning ("Inno Setup build was skipped: {0}" -f $_.Exception.Message)
        }
    } else {
        Write-Warning 'Inno Setup 6 was not found. The portable WaitLAB.exe was built; installer build was skipped.'
    }

    $Installer = Join-Path $ReleaseDir "WaitLAB-Setup-$ProjectVersion.exe"
    $HashTargets = @((Join-Path $ReleaseDir 'WaitLAB.exe'))
    if (Test-Path -LiteralPath $Installer) {
        $HashTargets += $Installer
    }
    $Hashes = Get-FileHash $HashTargets -Algorithm SHA256
    $HashLines = $Hashes | ForEach-Object { '{0} *{1}' -f $_.Hash, (Split-Path -Leaf $_.Path) }
    Set-Content -LiteralPath (Join-Path $ReleaseDir 'SHA256SUMS.txt') -Value $HashLines -Encoding ascii
    $Hashes
} finally {
    Pop-Location
}
