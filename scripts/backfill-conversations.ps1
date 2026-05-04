$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$serviceRoot = Join-Path $repoRoot "service"

if (Test-Path (Join-Path $repoRoot ".venv\Scripts\python.exe")) {
    $python = Join-Path $repoRoot ".venv\Scripts\python.exe"
} else {
    $python = "python"
}

Set-Location $serviceRoot
& $python -m app.backfill
