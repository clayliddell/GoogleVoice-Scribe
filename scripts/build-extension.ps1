$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$extensionRoot = Join-Path $repoRoot "extension"
$distRoot = Join-Path $repoRoot "dist"
$buildRoot = Join-Path $repoRoot "build\extension-crx"
$secretsRoot = Join-Path $repoRoot "build\secrets"
$manifestPath = Join-Path $extensionRoot "manifest.json"

if (-not (Test-Path $manifestPath)) {
    throw "Missing extension manifest: $manifestPath"
}

$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$version = [string]$manifest.version
if (-not $version) {
    throw "Extension manifest is missing a version."
}

$versionFile = Join-Path $repoRoot "service\app\version.py"
$versionMatch = Select-String -Path $versionFile -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $versionMatch) {
    throw "Could not read app version from $versionFile"
}
$appVersion = $versionMatch.Matches[0].Groups[1].Value
if ($version -ne $appVersion) {
    throw "Extension version $version does not match app version $appVersion."
}

$requiredFiles = @(
    "manifest.json",
    "background.js",
    "offscreen.html",
    "offscreen.js",
    "content\google_voice.js",
    "ui\permission.html",
    "ui\permission.js"
)

foreach ($relativePath in $requiredFiles) {
    $path = Join-Path $extensionRoot $relativePath
    if (-not (Test-Path $path)) {
        throw "Missing required extension file: $relativePath"
    }
}

$browserCandidates = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
)
$browser = $browserCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) {
    $command = Get-Command chrome, msedge -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) {
        $browser = $command.Source
    }
}
if (-not $browser) {
    throw "Could not find Chrome or Edge for CRX packaging."
}

New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
New-Item -ItemType Directory -Path $secretsRoot -Force | Out-Null

$stageRoot = Join-Path $buildRoot "GoogleVoiceScribeExtension"
$browserProfile = Join-Path $buildRoot "browser-profile"
$keyPath = Join-Path $secretsRoot "googlevoice-scribe.pem"
$generatedCrx = "$stageRoot.crx"
$generatedPem = "$stageRoot.pem"

if (Test-Path $stageRoot) {
    Remove-Item -LiteralPath $stageRoot -Recurse -Force
}
if (Test-Path $browserProfile) {
    Remove-Item -LiteralPath $browserProfile -Recurse -Force
}
Remove-Item -LiteralPath $generatedCrx -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $generatedPem -Force -ErrorAction SilentlyContinue

Copy-Item -Path $extensionRoot -Destination $stageRoot -Recurse -Force

$arguments = @(
    "--user-data-dir=$browserProfile",
    "--pack-extension=$stageRoot"
)
if (Test-Path $keyPath) {
    $arguments += "--pack-extension-key=$keyPath"
}

& $browser @arguments
$exitCode = $LASTEXITCODE
if ($null -ne $exitCode -and $exitCode -ne 0 -and -not (Test-Path $generatedCrx)) {
    throw "CRX packaging failed with exit code $exitCode."
}

for ($attempt = 0; $attempt -lt 60 -and -not (Test-Path $generatedCrx); $attempt++) {
    Start-Sleep -Milliseconds 500
}

if (-not (Test-Path $keyPath) -and (Test-Path $generatedPem)) {
    Move-Item -Path $generatedPem -Destination $keyPath -Force
}
if (-not (Test-Path $generatedCrx)) {
    throw "Browser did not create expected CRX: $generatedCrx"
}

$crxPath = Join-Path $distRoot "GoogleVoiceScribeExtension-v$version.crx"
if (Test-Path $crxPath) {
    Remove-Item -LiteralPath $crxPath -Force
}
Move-Item -Path $generatedCrx -Destination $crxPath -Force
Write-Output "Built $crxPath"
Write-Output "CRX key: $keyPath"
