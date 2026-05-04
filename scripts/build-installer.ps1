$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$distRoot = Join-Path $repoRoot "dist"
$pyinstallerRoot = Join-Path $repoRoot "build\pyinstaller"
$payloadRoot = Join-Path $repoRoot "build\installer-payload\payload"

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

$controlDist = Join-Path $pyinstallerRoot "control-dist"
$controlWork = Join-Path $pyinstallerRoot "control-work"
$installerDist = Join-Path $pyinstallerRoot "installer-dist"
$installerWork = Join-Path $pyinstallerRoot "installer-work"
$specDir = Join-Path $pyinstallerRoot "spec"

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name GoogleVoiceScribe `
    --distpath $controlDist `
    --workpath $controlWork `
    --specpath $specDir `
    (Join-Path $repoRoot "service\control_app.py")

$controlExe = Join-Path $controlDist "GoogleVoiceScribe.exe"
if (-not (Test-Path $controlExe)) {
    throw "PyInstaller did not create $controlExe"
}

if (Test-Path $payloadRoot) {
    Remove-Item -LiteralPath $payloadRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $payloadRoot -Force | Out-Null

Copy-Item -Path $controlExe -Destination (Join-Path $payloadRoot "GoogleVoiceScribe.exe") -Force
Copy-Item -Path (Join-Path $repoRoot "service") -Destination (Join-Path $payloadRoot "service") -Recurse -Force
Copy-Item -Path (Join-Path $repoRoot "scripts") -Destination (Join-Path $payloadRoot "scripts") -Recurse -Force
Copy-Item -Path (Join-Path $repoRoot "README.md") -Destination $payloadRoot -Force
Copy-Item -Path (Join-Path $repoRoot "LICENSE") -Destination $payloadRoot -Force
Copy-Item -Path (Join-Path $repoRoot "THIRD_PARTY_NOTICES.md") -Destination $payloadRoot -Force
Copy-Item -Path (Join-Path $repoRoot ".env.example") -Destination $payloadRoot -Force

$crx = Join-Path $distRoot "GoogleVoiceScribeExtension-v$version.crx"
if (Test-Path $crx) {
    $payloadExtensionDir = Join-Path $payloadRoot "extension"
    New-Item -ItemType Directory -Path $payloadExtensionDir -Force | Out-Null
    Copy-Item -Path $crx -Destination $payloadExtensionDir -Force
}

Get-ChildItem -Path $payloadRoot -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem -Path $payloadRoot -Recurse -File -Include "*.pyc","*.pyo" | Remove-Item -Force

$payloadArg = "$payloadRoot;payload"
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "GoogleVoiceScribeSetup-v$version-win-x64" `
    --distpath $distRoot `
    --workpath $installerWork `
    --specpath $specDir `
    --add-data $payloadArg `
    (Join-Path $repoRoot "service\installer.py")

$installerPath = Join-Path $distRoot "GoogleVoiceScribeSetup-v$version-win-x64.exe"
if (-not (Test-Path $installerPath)) {
    throw "PyInstaller did not create $installerPath"
}

$sizeBytes = (Get-Item $installerPath).Length
$limitBytes = 2GB
if ($sizeBytes -ge $limitBytes) {
    throw "Release asset exceeds GitHub's 2 GiB per-file limit: $installerPath"
}

Write-Output "Built $installerPath"
