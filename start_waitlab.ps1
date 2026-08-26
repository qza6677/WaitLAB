$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pythonw = Get-Command pythonw -ErrorAction SilentlyContinue
if ($null -eq $Pythonw) {
    throw 'pythonw was not found. Install Python and requirements.txt first.'
}
Start-Process -FilePath $Pythonw.Source -ArgumentList (Join-Path $ProjectRoot 'run_waitlab.py') -WindowStyle Hidden
