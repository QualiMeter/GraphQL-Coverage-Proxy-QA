$ErrorActionPreference = 'Stop'
$testPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $testPython)) {
    throw 'Install dependencies into .venv as described in README.md.'
}
& $testPython (Join-Path $PSScriptRoot 'run_tests.py') @args
exit $LASTEXITCODE
