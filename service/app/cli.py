from __future__ import annotations

import argparse
from typing import Sequence

import uvicorn

from .config import Settings
from .version import APP_NAME, __version__


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=f"Run the {APP_NAME} local transcription service.")
    parser.add_argument("--host", default=settings.host, help=f"Bind host. Defaults to {settings.host!r}.")
    parser.add_argument("--port", default=settings.port, type=int, help=f"Bind port. Defaults to {settings.port}.")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload for local development.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
