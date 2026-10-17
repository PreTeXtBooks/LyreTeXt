# LyreTeXt desktop (Phase 0 spike)

An Electron shell that runs the LyreTeXt backend as a sidecar and shows the
existing web UI in its own window. Windows first; unsigned. See #63 (architecture),
#68 (this spike) and #75 (PyInstaller decision).

## How it works

`main.js` takes a single-instance lock, then `backend-process.js`:

1. picks a free port on `127.0.0.1`;
2. starts the backend with `--host 127.0.0.1 --port <port> --data-dir
   %APPDATA%\LyreTeXt\data`, appending its output to
   `%APPDATA%\LyreTeXt\backend.log`. The data dir holds everything the backend
   writes: `config.yml` (seeded from the bundled default on first launch, then
   the file the Settings page edits and runs read), `lyretext_runs.db`,
   `output/` and `temp/`. Nothing is written into the install dir. The
   backend's cwd is also set to the data dir, for the tools it runs (knitr
   writes figures relative to R's working directory);
3. polls `GET /api/health` until it returns 200;
4. opens a window at `http://127.0.0.1:<port>/ui/`.

Tool locations can be injected through the environment, which a bundled
toolchain will use: `LYRETEXT_PANDOC_EXE`, `LYRETEXT_PANDOC_WRITER` (the
`pretext.lua` writer) and `LYRETEXT_RSCRIPT_EXE`. Each falls back to a PATH
lookup.

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
dist/lyretext-backend/lyretext-backend.exe --self-check   # verify the frozen bundle
```

`--self-check` imports the app, checks that TLS verifies against the Windows
certificate store (truststore, #90; certifi alone fails behind proxies and
antivirus that intercept TLS), loads inferenceswitch's model cards and builds
both provider adapters (which import the provider SDKs dynamically), then
exits. Mock runs never touch these, so this is the check that a frozen bundle
can actually make LLM calls. Add `--live` to also make one minimal structured
call per provider whose API key is set in the environment.

`npm install` on npm 11+ skips Electron's install script, so the Electron
binary is never downloaded. Run `node node_modules/electron/install.js` once
afterwards.

`npm start` looks for `.venv` in the repo root or any parent (worktrees share the
main checkout's venv); set `LYRETEXT_PYTHON` to override. Environment variables
are passed through to the backend, so `LYRETEXT_MOCK=1` runs the mock pipeline
with no API keys.

`npm run freeze` needs `pyinstaller` in the venv. In the spike build it also
bundles the gitignored `demo/temp_output` and `demo/example_output` (found by
walking up from the repo, or via `LYRETEXT_DEMO_DIR`) so the frozen app can run
`LYRETEXT_MOCK=1`; a release build would drop them.
