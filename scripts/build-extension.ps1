$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$extensionRoot = Join-Path $repoRoot "extension"
$distRoot = Join-Path $repoRoot "dist"
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

New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
$stageRoot = Join-Path $distRoot "extension-package"
if (Test-Path $stageRoot) {
    Remove-Item -LiteralPath $stageRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

Copy-Item -Path (Join-Path $extensionRoot "*") -Destination $stageRoot -Recurse -Force

$zipPath = Join-Path $distRoot "GoogleVoiceScribeExtension-v$version.zip"
if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

Compress-Archive -Path (Join-Path $stageRoot "*") -DestinationPath $zipPath -Force
Write-Output "Built $zipPath"
