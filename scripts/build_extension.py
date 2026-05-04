from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from script_common import BUILD_ROOT, DIST_ROOT, REPO_ROOT, copytree_clean, read_app_version, read_json, remove_path


REQUIRED_FILES = (
    "manifest.json",
    "background.js",
    "offscreen.html",
    "offscreen.js",
    "content/google_voice.js",
    "ui/permission.html",
    "ui/permission.js",
)


def main() -> int:
    extension_root = REPO_ROOT / "extension"
    manifest_path = extension_root / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"Missing extension manifest: {manifest_path}")

    manifest = read_json(manifest_path)
    version = str(manifest.get("version") or "")
    if not version:
        raise SystemExit("Extension manifest is missing a version.")
    app_version = read_app_version()
    if version != app_version:
        raise SystemExit(f"Extension version {version} does not match app version {app_version}.")

    for relative_path in REQUIRED_FILES:
        path = extension_root / relative_path
        if not path.exists():
            raise SystemExit(f"Missing required extension file: {relative_path}")

    browser = find_browser()
    if not browser:
        raise SystemExit("Could not find Chrome or Edge for CRX packaging.")

    build_root = BUILD_ROOT / "extension-crx"
    secrets_root = BUILD_ROOT / "secrets"
    stage_root = build_root / "GoogleVoiceScribeExtension"
    browser_profile = build_root / "browser-profile"
    key_path = secrets_root / "googlevoice-scribe.pem"
    generated_crx = stage_root.with_suffix(".crx")
    generated_pem = stage_root.with_suffix(".pem")

    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    build_root.mkdir(parents=True, exist_ok=True)
    secrets_root.mkdir(parents=True, exist_ok=True)
    remove_path(browser_profile)
    remove_path(generated_crx)
    remove_path(generated_pem)
    copytree_clean(extension_root, stage_root)

    arguments = [browser, f"--user-data-dir={browser_profile}", f"--pack-extension={stage_root}"]
    if key_path.exists():
        arguments.append(f"--pack-extension-key={key_path}")
    completed = subprocess.run(arguments, check=False)
    if completed.returncode not in (0, None) and not generated_crx.exists():
        raise SystemExit(f"CRX packaging failed with exit code {completed.returncode}.")

    wait_for_file(generated_crx)
    if not key_path.exists() and generated_pem.exists():
        shutil.move(str(generated_pem), str(key_path))
    if not generated_crx.exists():
        raise SystemExit(f"Browser did not create expected CRX: {generated_crx}")

    crx_path = DIST_ROOT / f"GoogleVoiceScribeExtension-v{version}.crx"
    remove_path(crx_path)
    shutil.move(str(generated_crx), str(crx_path))
    print(f"Built {crx_path}")
    print(f"CRX key: {key_path}")
    return 0


def find_browser() -> str | None:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    for name in ("chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def wait_for_file(path: Path) -> None:
    for _attempt in range(60):
        if path.exists():
            return
        time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
