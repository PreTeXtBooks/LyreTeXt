"""Desktop entry point for the LyreTeXt backend.

Used both as the PyInstaller script (frozen) and directly by the Electron
shell in dev mode (`python desktop/backend/entry.py --host ... --port ...`).
It adds two things on top of `python -m lyretext`, without touching the
package:

* A parent watchdog. Electron gives the backend a stdin pipe it never writes
  to. If Electron dies without running its exit handlers (crash, Task Manager
  kill), the pipe closes, the read below returns EOF, and the backend exits
  instead of lingering as an orphan.
* Static imports of modules uvicorn only names as strings, so PyInstaller's
  import analysis finds them.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path


def _start_parent_watchdog() -> None:
    stdin = sys.stdin
    if stdin is None or stdin.isatty():
        return  # launched by hand from a terminal; nobody to watch

    def _watch() -> None:
        try:
            while stdin.buffer.read(4096):
                pass
        except (OSError, ValueError):
            pass
        os._exit(0)

    threading.Thread(target=_watch, name="parent-watchdog", daemon=True).start()


def main() -> None:
    if not getattr(sys, "frozen", False):
        # Dev mode: make the repo's lyretext package importable.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    _start_parent_watchdog()

    import lyretext.api  # noqa: F401  (uvicorn imports "lyretext.api:app" by string)
    from lyretext.__main__ import main as lyretext_main

    lyretext_main()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
