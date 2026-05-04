from __future__ import annotations

import argparse
import os
import py_compile
import subprocess
import sys

from script_common import REPO_ROOT, SERVICE_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description="Run lightweight CI validation.")
    parser.add_argument("--build-extension", action="store_true")
    args = parser.parse_args()

    for path in SERVICE_ROOT.rglob("*.py"):
        py_compile.compile(str(path), doraise=True)
    for path in (REPO_ROOT / "scripts").glob("*.py"):
        py_compile.compile(str(path), doraise=True)
    print("python compile ok")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(SERVICE_ROOT)
    subprocess.run([sys.executable, "-m", "pytest"], cwd=REPO_ROOT, env=env, check=True)
    if args.build_extension:
        subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "build_extension.py")], cwd=REPO_ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
