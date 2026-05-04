from __future__ import annotations

import os
import subprocess
import sys

from script_common import SERVICE_ROOT, repo_python


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SERVICE_ROOT)
    return subprocess.call([str(repo_python()), "-m", "app.compress_recordings", *sys.argv[1:]], cwd=SERVICE_ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
