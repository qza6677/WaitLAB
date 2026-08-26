$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $ProjectRoot
try {
    python -m waitlab.hook_installer uninstall
}
finally {
    Pop-Location
}

