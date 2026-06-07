$ErrorActionPreference = "Stop"

$ApiRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ApiRoot

uv run uvicorn app.main:app --host 127.0.0.1 --port 5178 --reload
