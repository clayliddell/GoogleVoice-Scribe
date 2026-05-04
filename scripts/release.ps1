param(
    [string]$RepoName = "GoogleVoice-Scribe",
    [string]$Tag = "v0.2.0",
    [switch]$SkipBuild,
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not $AllowDirty) {
    $dirty = git status --porcelain
    if ($dirty) {
        throw "Working tree is dirty. Commit or stash changes before releasing, or pass -AllowDirty."
    }
}

if (-not $SkipBuild) {
    & (Join-Path $repoRoot "scripts\build-extension.ps1")
    & (Join-Path $repoRoot "scripts\build-installer.ps1")
}

$ghCommand = Get-Command gh -ErrorAction SilentlyContinue
$gh = if ($ghCommand) { $ghCommand.Source } else { $null }
if (-not $gh) {
    $candidate = "C:\Program Files\GitHub CLI\gh.exe"
    if (Test-Path $candidate) {
        $gh = $candidate
    }
}
if (-not $gh) {
    throw "GitHub CLI was not found. Install gh or add it to PATH."
}

& $gh auth status
$owner = (& $gh api user --jq ".login").Trim()
$repo = "$owner/$RepoName"

try {
    & $gh repo view $repo *> $null
    $repoViewExit = $LASTEXITCODE
} catch {
    $repoViewExit = 1
}
if ($repoViewExit -ne 0) {
    & $gh repo create $RepoName --public --source $repoRoot --remote origin --description "Chromium extension and local Windows server for Google Voice transcription." --push
} else {
    $origin = git remote get-url origin 2>$null
    if (-not $origin) {
        git remote add origin "https://github.com/$repo.git"
    }
    git push -u origin master
}

$tagExists = git tag --list $Tag
if (-not $tagExists) {
    git tag -a $Tag -m "GoogleVoice Scribe $Tag"
}
git push origin $Tag

$installerExe = Get-ChildItem -Path (Join-Path $repoRoot "dist") -Filter "GoogleVoiceScribeSetup-*-win-x64.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$extensionCrx = Get-ChildItem -Path (Join-Path $repoRoot "dist") -Filter "GoogleVoiceScribeExtension-*.crx" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installerExe -or -not $extensionCrx) {
    throw "Missing release assets under dist/."
}

try {
    & $gh release view $Tag --repo $repo *> $null
    $releaseViewExit = $LASTEXITCODE
} catch {
    $releaseViewExit = 1
}
if ($releaseViewExit -ne 0) {
    & $gh release create $Tag $installerExe.FullName $extensionCrx.FullName --repo $repo --title "GoogleVoice Scribe $Tag" --notes-file (Join-Path $repoRoot "RELEASE_NOTES.md")
} else {
    & $gh release upload $Tag $installerExe.FullName $extensionCrx.FullName --repo $repo --clobber
}

Write-Output "Released https://github.com/$repo/releases/tag/$Tag"
