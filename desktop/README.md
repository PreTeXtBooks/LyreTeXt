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

Tool locations can be injected through the environment:
`LYRETEXT_PANDOC_EXE`, `LYRETEXT_PANDOC_WRITER` (the `pretext.lua` writer) and
`LYRETEXT_RSCRIPT_EXE`. Each falls back to a PATH lookup.

## Bundled toolchain and R (#70, decisions #63, #65)

The packaged app ships **pandoc** and Oscar Levin's **pretext.lua** writer
(<https://github.com/oscarlevin/pandoc-pretext>) in `resources/toolchain`:

```
toolchain/
  toolchain.json          what was staged: pandoc version, file hashes, sources
  NOTICE.txt              third-party notice
  pandoc/                 pandoc.exe, COPYRIGHT.txt, COPYING.rtf (GPL), SOURCE.txt
  pretext-writer/         pretext.lua, pretext-environments.lua (loaded by
                          pretext.lua), pretext-latex-reader.lua, LICENSE (GPL-2.0), SOURCE.txt
```

`npm run toolchain` (`scripts/stage-toolchain.js`, also run by `npm run pack`)
stages it into `dist/toolchain`, and electron-builder copies that to
`resources/toolchain` (`extraResources`). Its sources are configuration, so CI
can supply a pandoc it downloaded:

| Input | Flag | Env var | Default |
|---|---|---|---|
| pandoc executable | `--pandoc <file>` | `LYRETEXT_TOOLCHAIN_PANDOC` | `pandoc` on PATH |
| writer folder | `--writer-dir <dir>` | `LYRETEXT_TOOLCHAIN_WRITER_DIR` | `<pandoc user data dir>/custom` |
| output | `--out <dir>` | | `dist/toolchain` |

pandoc must be 3.0 or later. `toolchain/toolchain.json` records the verified
pandoc version and the pandoc-pretext commit and file hashes the writer was
checked against; staging warns when what it is given differs. A `LICENSE` in
the writer folder (an upstream checkout has one) is shipped as-is, otherwise the
vendored `toolchain/licenses/GPL-2.0.txt`.

When packaged, `backend-process.js` sets `LYRETEXT_PANDOC_EXE`,
`LYRETEXT_PANDOC_WRITER` and `LYRETEXT_TOOLCHAIN_DIR` to the bundled copies,
unless the first two are already set in the environment (an escape hatch for
diagnosing a broken bundle). `npm start` and `start:frozen` keep the PATH
lookup. `backend.log` records which applied.

**R is not bundled** (#65). The backend runs `Rscript` once (the configured
path or PATH, then the usual install folders) for R's version and whether knitr
is installed, with a timeout that kills the process tree, so a hung R cannot
stall anything. Starting an R Markdown run without R or knitr returns 422 with
where to get R (<https://cran.r-project.org/bin/windows/base/>) and
`install.packages("knitr")`; the Rmd pipeline raises the same guidance if it
gets that far.

**System check.** `GET /api/environment` (behind the local-API token) reports
pandoc (version, bundled or PATH), the writer (found, and a tiny LaTeX →
PreTeXt test conversion), R, knitr, and API keys set/unset, never a value.
Settings shows it as *System check*; New Run repeats anything that needs
attention.

On quit, last-window-close or an uncaught error, the backend's process tree is
killed with `taskkill /pid <pid> /T /F`. If Electron itself is killed and never
runs those handlers, `backend/entry.py`'s watchdog sees its stdin pipe close
and exits the backend.

## API keys and the local API (#67, decision #84)

Each user runs LyreTeXt with their own provider keys, held only on their machine.

- **Entry.** Settings → *API keys* (shown only in the app). The page talks to
  `window.lyretextDesktop`, exposed by `preload.js` through `contextBridge`:
  `listKeys()` (provider + set/unset, never a value), `setKey`, `deleteKey`,
  `testKey`. Each is an `ipcRenderer.invoke` answered by `main.js`, which only
  serves the app's own page.
- **Storage.** `keys.js` encrypts each key with Electron's `safeStorage` (DPAPI
  on Windows) and keeps a base64 blob per provider in
  `%APPDATA%\LyreTeXt\keys.json`. If encryption is unavailable it refuses to
  store rather than write plaintext. Providers: `gemini` → `GEMINI_API_KEY`,
  `anthropic` → `ANTHROPIC_API_KEY`.
- **Into the backend.** At launch the backend's environment is the parent's
  with **every `*_API_KEY` variable removed**, plus the stored keys and
  `LYRETEXT_AUTH_TOKEN`. In that mode the backend never reads `.env`. After a
  save or remove, the main process POSTs the change to
  `/api/internal/keys` (token header only), which updates the backend's
  environment and resets the cached inferenceswitch client, so no restart is
  needed. *Test* calls `/api/keys/verify`, which never echoes the key.
- **Local API protection.** `LYRETEXT_AUTH_TOKEN` is 32 random bytes, new each
  launch. The window carries it as an `HttpOnly`, `SameSite=Strict` cookie set
  before `/ui/` loads; the main process sends it as `X-LyreTeXt-Token`. With
  the token set, the backend (`lyretext/security.py`) answers 401 to any
  request without it (except `GET /api/health`), 400 to any `Host` other than
  `127.0.0.1:<port>` / `localhost:<port>`, sends no CORS headers, and rejects
  run `output_dir`/`temp_dir` values outside the data dir.
- **Logs.** A log filter masks every `*_API_KEY` value and the token in all
  log records, so neither reaches `backend.log`.

Without a token (`python -m lyretext`, the browser workflow) none of this
applies: keys come from `.env` and the API needs no auth.

## Running

```bash
npm install                    # in desktop/
npm start                      # backend from the project venv (python backend/entry.py)
npm run freeze                 # PyInstaller -> desktop/dist/lyretext-backend (activate the venv first)
npm run start:frozen           # Electron + the frozen backend
npm run toolchain              # stage pandoc + pretext.lua -> desktop/dist/toolchain
npm run pack                   # toolchain, then electron-builder --win --dir -> desktop/release/win-unpacked
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
are passed through to the backend (except `*_API_KEY`, see above), so
`LYRETEXT_MOCK=1` runs the mock pipeline with no API keys.

`npm run freeze` needs `pyinstaller` in the venv. In the spike build it also
bundles the gitignored `demo/temp_output` and `demo/example_output` (found by
walking up from the repo, or via `LYRETEXT_DEMO_DIR`) so the frozen app can run
`LYRETEXT_MOCK=1`; a release build would drop them.
