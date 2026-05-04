from __future__ import annotations

import argparse
import os
import subprocess

from script_common import SERVICE_ROOT, repo_python


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the GoogleVoice Scribe local server.")
    parser.add_argument("--host", default=os.getenv("GV_SERVICE_HOST", "127.0.0.1"))
    parser.add_argument("--port", default=os.getenv("GV_SERVICE_PORT", "8765"))
    args, service_args = parser.parse_known_args()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(SERVICE_ROOT)
    command = [
        str(repo_python()),
        "-m",
        "app.cli",
        "--host",
        str(args.host),
        "--port",
        str(args.port),
        *service_args,
    ]
    return subprocess.call(command, cwd=SERVICE_ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
