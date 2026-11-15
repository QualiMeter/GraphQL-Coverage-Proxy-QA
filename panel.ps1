$ErrorActionPreference = 'Stop'
$panelPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $panelPython)) {
    throw 'Install dependencies as described in README.md.'
}
& $panelPython (Join-Path $PSScriptRoot 'control_panel.py') @args
exit $LASTEXITCODE
