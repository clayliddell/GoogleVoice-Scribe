$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$distRoot = Join-Path $repoRoot "dist"
$pyinstallerRoot = Join-Path $repoRoot "build\pyinstaller"

if (-not (Test-Path $python)) {
    throw "Missing .venv. Create it first with: py -3.12 -m venv .venv"
}

$versionFile = Join-Path $repoRoot "service\app\version.py"
$versionMatch = Select-String -Path $versionFile -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $versionMatch) {
    throw "Could not read app version from $versionFile"
}
$version = $versionMatch.Matches[0].Groups[1].Value

New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
New-Item -ItemType Directory -Path $pyinstallerRoot -Force | Out-Null

& $python -m pip install "pyinstaller>=6.11"

$launcher = Join-Path $repoRoot "service\launcher.py"
$exeDist = Join-Path $pyinstallerRoot "dist"
$exeWork = Join-Path $pyinstallerRoot "work"
$specDir = Join-Path $pyinstallerRoot "spec"

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name GoogleVoiceScribeServer `
    --distpath $exeDist `
    --workpath $exeWork `
    --specpath $specDir `
    $launcher

$exePath = Join-Path $exeDist "GoogleVoiceScribeServer.exe"
if (-not (Test-Path $exePath)) {
    throw "PyInstaller did not create $exePath"
}

$stageRoot = Join-Path $distRoot "GoogleVoiceScribeServer-v$version-win-x64"
if (Test-Path $stageRoot) {
    Remove-Item -LiteralPath $stageRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

Copy-Item -Path $exePath -Destination (Join-Path $stageRoot "GoogleVoiceScribeServer.exe") -Force
Copy-Item -Path (Join-Path $repoRoot "service") -Destination (Join-Path $stageRoot "service") -Recurse -Force
Copy-Item -Path (Join-Path $repoRoot "scripts") -Destination (Join-Path $stageRoot "scripts") -Recurse -Force
Copy-Item -Path (Join-Path $repoRoot "README.md") -Destination $stageRoot -Force
Copy-Item -Path (Join-Path $repoRoot "LICENSE") -Destination $stageRoot -Force
Copy-Item -Path (Join-Path $repoRoot "THIRD_PARTY_NOTICES.md") -Destination $stageRoot -Force
Copy-Item -Path (Join-Path $repoRoot ".env.example") -Destination $stageRoot -Force

Get-ChildItem -Path $stageRoot -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem -Path $stageRoot -Recurse -File -Include "*.pyc","*.pyo" | Remove-Item -Force

$zipPath = Join-Path $distRoot "GoogleVoiceScribeServer-v$version-win-x64.zip"
if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path (Join-Path $stageRoot "*") -DestinationPath $zipPath -Force

$sizeBytes = (Get-Item $zipPath).Length
$limitBytes = 2GB
if ($sizeBytes -ge $limitBytes) {
    throw "Release asset exceeds GitHub's 2 GiB per-file limit: $zipPath"
}

Write-Output "Built $zipPath"
