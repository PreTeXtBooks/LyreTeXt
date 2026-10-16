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
* `--self-check [--live]`: verify the bundle's dynamically loaded pieces
  (inferenceswitch's adapters, model cards and provider SDKs) and exit.
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


def _self_check(live: bool) -> int:
    """Exercise what PyInstaller's import analysis can miss, then exit.

    `lyretext-backend --self-check` imports the app, loads inferenceswitch's
    model cards (package data read through importlib.resources) and builds
    both provider adapters with a dummy key, which imports each provider SDK
    via importlib (require_sdk). Mock runs never reach any of this, so without
    the check a bundle missing them only fails on a user's first real run.

    With `--live`, also makes one minimal structured call per provider whose
    key is set in the environment. The key is never printed.
    """
    import lyretext.api  # noqa: F401  (the whole app imports)
    from inferenceswitch import Client
    from inferenceswitch.adapters import build_adapter

    from lyretext.config import create_llm

    dummy = "self-check-dummy-key"
    client = Client(key_provider=lambda spec: dummy)
    print(f"ok model cards: {len(list(client.cards))} (updated {client.cards.updated})")
    for name in ("anthropic", "gemini"):
        adapter = build_adapter(client.registry.get(name), dummy)
        sdk = type(adapter.raw_client)
        print(f"ok {name}: {type(adapter).__name__} -> {sdk.__module__}.{sdk.__qualname__}")

    if live:
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }
        for name, key_env in (("gemini", "GEMINI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")):
            if not os.environ.get(key_env):
                print(f"skip live {name}: {key_env} not set")
                continue
            llm = create_llm(name)
            result = llm.with_structured_output(schema).invoke(
                ["Reply with the single word 'pong' as the answer."]
            )
            print(f"ok live {name} ({llm.model}): {result!r}")
    return 0


def main() -> None:
    if not getattr(sys, "frozen", False):
        # Dev mode: make the repo's lyretext package importable.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    if "--self-check" in sys.argv[1:]:
        sys.exit(_self_check(live="--live" in sys.argv[1:]))

    _start_parent_watchdog()

    import lyretext.api  # noqa: F401  (uvicorn imports "lyretext.api:app" by string)
    from lyretext.__main__ import main as lyretext_main

    lyretext_main()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
