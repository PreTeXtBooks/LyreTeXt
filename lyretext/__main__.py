"""Entry point: python -m lyretext [--host HOST] [--port PORT] [--reload] [--data-dir DIR]"""
from __future__ import annotations

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="LyreTeXt server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Hot-reload (dev mode)")
    parser.add_argument(
        "--data-dir",
        help=(
            "Writable directory for config.yml, the runs database, output/ and "
            "temp/ (default: $LYRETEXT_DATA_DIR, else the repo's config.yml and "
            "the working directory)"
        ),
    )
    args = parser.parse_args()

    if args.data_dir:
        # Published through the environment rather than passed down, so the
        # one resolver (lyretext.config.data_paths) sees it — including in the
        # worker process uvicorn spawns under --reload.
        from .config import DATA_DIR_ENV, data_paths

        os.environ[DATA_DIR_ENV] = os.path.abspath(args.data_dir)
        # Create and seed it now: the user can find config.yml straight away,
        # and an unwritable data dir fails here rather than on a first request.
        data_paths()

    try:
        import uvicorn
    except ImportError:
        print("uvicorn is required: pip install uvicorn[standard]", file=sys.stderr)
        sys.exit(1)

    uvicorn.run(
        "lyretext.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
