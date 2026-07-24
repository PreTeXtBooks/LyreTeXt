"""Entry point: python -m lyretext [--host HOST] [--port PORT] [--reload]"""
from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="LyreTeXt server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Hot-reload (dev mode)")
    args = parser.parse_args()

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
