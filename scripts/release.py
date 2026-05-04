from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from script_common import DIST_ROOT, REPO_ROOT, run, run_capture


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and publish a GoogleVoice Scribe GitHub release.")
    parser.add_argument("--repo-name", default="GoogleVoice-Scribe")
    parser.add_argument("--tag", default="v0.2.0")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    if not args.allow_dirty and run_capture(["git", "status", "--porcelain"]).strip():
        raise SystemExit("Working tree is dirty. Commit or stash changes before releasing, or pass --allow-dirty.")

    if not args.skip_build:
        run([sys.executable, REPO_ROOT / "scripts" / "build_extension.py"])
        run([sys.executable, REPO_ROOT / "scripts" / "build_installer.py"])

    gh = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
    if not Path(gh).exists() and not shutil.which(gh):
        raise SystemExit("GitHub CLI was not found. Install gh or add it to PATH.")

    run([gh, "auth", "status"])
    owner = run_capture([gh, "api", "user", "--jq", ".login"]).strip()
    repo = f"{owner}/{args.repo_name}"

    repo_exists = subprocess.run([gh, "repo", "view", repo], cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not repo_exists:
        run(
            [
                gh,
                "repo",
                "create",
                args.repo_name,
                "--public",
                "--source",
                REPO_ROOT,
                "--remote",
                "origin",
                "--description",
                "Chromium extension and local Windows server for Google Voice transcription.",
                "--push",
            ]
        )
    else:
        origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        if origin.returncode != 0 or not origin.stdout.strip():
            run(["git", "remote", "add", "origin", f"https://github.com/{repo}.git"])
        run(["git", "push", "-u", "origin", "master"])

    if args.tag not in run_capture(["git", "tag", "--list", args.tag]).splitlines():
        run(["git", "tag", "-a", args.tag, "-m", f"GoogleVoice Scribe {args.tag}"])
    run(["git", "push", "origin", args.tag])

    installer = newest(DIST_ROOT.glob("GoogleVoiceScribeSetup-*-win-x64.exe"))
    crx = newest(DIST_ROOT.glob("GoogleVoiceScribeExtension-*.crx"))
    if not installer or not crx:
        raise SystemExit("Missing release assets under dist/.")

    release_exists = subprocess.run([gh, "release", "view", args.tag, "--repo", repo], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if release_exists:
        run([gh, "release", "upload", args.tag, installer, crx, "--repo", repo, "--clobber"])
    else:
        run(
            [
                gh,
                "release",
                "create",
                args.tag,
                installer,
                crx,
                "--repo",
                repo,
                "--title",
                f"GoogleVoice Scribe {args.tag}",
                "--notes-file",
                REPO_ROOT / "RELEASE_NOTES.md",
            ]
        )
    print(f"Released https://github.com/{repo}/releases/tag/{args.tag}")
    return 0


def newest(paths) -> Path | None:
    paths = [Path(path) for path in paths]
    if not paths:
        return None
    return max(paths, key=lambda path: path.stat().st_mtime)


if __name__ == "__main__":
    raise SystemExit(main())
