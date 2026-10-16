# LyreTeXt desktop (Phase 0 spike)

An Electron shell that runs the LyreTeXt backend as a sidecar and shows the
existing web UI in its own window. Windows first; unsigned. See #63 (architecture),
#68 (this spike) and #75 (PyInstaller decision).

## How it works

`main.js` takes a single-instance lock, then `backend-process.js`:

1. picks a free port on `127.0.0.1`;
2. starts the backend with `--host 127.0.0.1 --port <port>`, with its working
   directory set to `%APPDATA%\LyreTeXt\backend` (where `lyretext_runs.db`,
   `output/` and `temp/` end up) and its output appended to
   `%APPDATA%\LyreTeXt\backend.log`;
3. polls `GET /api/settings` until it returns 200;
4. opens a window at `http://127.0.0.1:<port>/ui/`.

On quit, last-window-close or an uncaught error, the backend's process tree is
killed with `taskkill /pid <pid> /T /F`. If Electron itself is killed and never
runs those handlers, `backend/entry.py`'s watchdog sees its stdin pipe close
and exits the backend.

## Running

```bash
npm install                    # in desktop/
npm start                      # backend from the project venv (python backend/entry.py)
npm run freeze                 # PyInstaller -> desktop/dist/lyretext-backend (activate the venv first)
npm run start:frozen           # Electron + the frozen backend
npm run pack                   # electron-builder --win --dir -> desktop/release/win-unpacked
```

`npm start` looks for `.venv` in the repo root or any parent (worktrees share the
main checkout's venv); set `LYRETEXT_PYTHON` to override. Environment variables
are passed through to the backend, so `LYRETEXT_MOCK=1` runs the mock pipeline
with no API keys.

`npm run freeze` needs `pyinstaller` in the venv. In the spike build it also
bundles the gitignored `demo/temp_output` and `demo/example_output` (found by
walking up from the repo, or via `LYRETEXT_DEMO_DIR`) so the frozen app can run
`LYRETEXT_MOCK=1`; a release build would drop them.
