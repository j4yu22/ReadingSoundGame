$ErrorActionPreference = "Stop"

$ApiRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ApiRoot

Write-Host "Open http://127.0.0.1:5178 after the server starts. Press Ctrl+C to stop."

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($UvCommand) {
    & $UvCommand.Source run --locked uvicorn app.main:app --host 127.0.0.1 --port 5178 --reload
} else {
    # A pip-installed uv can be available as a Python module before Scripts is on PATH.
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        throw "Install Python and uv, then run this script again."
    }

    & $PythonCommand.Source -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('uv') else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw "uv is missing. Run: python -m pip install --user uv, then run this script again."
    }

    & $PythonCommand.Source -m uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 5178 --reload
}
