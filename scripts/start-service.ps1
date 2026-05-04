$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$serviceRoot = Join-Path $repoRoot "service"

if (Test-Path (Join-Path $repoRoot ".venv\Scripts\python.exe")) {
    $python = Join-Path $repoRoot ".venv\Scripts\python.exe"
} else {
    $python = "python"
}

Set-Location $serviceRoot
$port = if ($env:GV_SERVICE_PORT) { $env:GV_SERVICE_PORT } else { "8765" }
& $python -m app.cli --host 127.0.0.1 --port $port
