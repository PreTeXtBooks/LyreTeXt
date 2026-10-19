# LyreTeXt desktop

An Electron shell that runs the LyreTeXt backend as a sidecar and shows the
existing web UI in its own window. Windows first; unsigned beta. See #63
(architecture), #68 (spike), #75 (PyInstaller) and #71 (installer and CI).

## Installing the beta (testers)

Download `LyreTeXt-Setup-<version>.exe` from the release (or from the
`LyreTeXt-installer` artifact of a CI run) and run it. It installs for the
current user only, into `%LOCALAPPDATA%\Programs\`, with no administrator rights
or UAC prompt, and adds Start-menu and desktop shortcuts.

**SmartScreen.** The beta is not code-signed yet (#66), so Windows shows
*"Windows protected your PC"*. Click **More info**, then **Run anyway**. Some
browsers also flag the download as uncommon: choose *Keep* (Edge: *…* → *Keep* →
*Show more* → *Keep anyway*).

**First launch is slow.** Windows Defender scans the freshly installed app, so
the first start can take 30 s or more (later starts take a few seconds). A
*Starting LyreTeXt…* window shows until the app is ready.

**Antivirus warnings (Norton, Defender and others).** Beta builds are
unsigned, and unsigned apps with few downloads are often flagged by antivirus
products on reputation alone (Norton reports these as e.g. `WS.Reputation.1`).
The app may be blocked when you download it, run the installer, or start
LyreTeXt, and a file may be moved to quarantine. Only make an exception for an
installer from the project's GitHub release or CI run, and check its SHA-256
against the release's `SHA256SUMS.txt` (`Get-FileHash <file>` in PowerShell).

- *Restore a blocked file.*
  - **Norton 360:** open Norton → **Security** → **History**. Set *Show* to
    *Quarantine* (or *Resolved Security Risks*), select the LyreTeXt item,
    then **Options** → **Restore & exclude this file**.
  - **Microsoft Defender:** Windows Security → **Virus & threat protection** →
    **Protection history**. Open the item, then **Actions** → **Restore** (or
    **Allow on device**).
- *Exclude the install folder*, `%LOCALAPPDATA%\Programs\lyretext-desktop`, so
  updates are not caught again.
  - **Norton 360:** **Settings** → **Antivirus** → **Scans and Risks** →
    *Items to Exclude from Scans* → **Configure** → **Add folders**. Do the
    same under *Items to Exclude from Auto-Protect, Script Control, SONAR and
    Download Intelligence Detection*.
  - **Microsoft Defender:** Windows Security → **Virus & threat protection** →
    *Virus & threat protection settings* → **Manage settings** → **Exclusions**
    → **Add or remove exclusions** → **Add an exclusion** → **Folder**. This
    needs administrator rights.
- *Report the false positive* so the vendor clears it for everyone: Norton at
  <https://submit.norton.com/> (choose *false positive*), Microsoft through the
  Defender file-submission portal at
  <https://www.microsoft.com/wdsi/filesubmission>. Give the file, the LyreTeXt
  version and the detection name, and say in the issue tracker that you did.

What the build already does to avoid false positives (#100): the backend's
PyInstaller bootloader is compiled from source in CI rather than being the
stock one shared by every PyInstaller app, both executables carry version
information (company, product, description, version), and nothing is
UPX-packed. These lower the odds of a heuristic detection but do not build
reputation. **Code signing is the lasting fix**, tracked in #66 and #72; until
then, expect some antivirus products to flag new beta versions.

**Uninstalling.** Settings → Apps → LyreTeXt → Uninstall. Your data, keys and
logs in `%APPDATA%\LyreTeXt` are kept; delete that folder to remove them too.

## How it works

`main.js` takes a single-instance lock, shows the *Starting LyreTeXt…* splash
(`splash.html`), then `backend-process.js`:

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
4. opens a window at `http://127.0.0.1:<port>/ui/`, shows it once the UI has
   loaded and closes the splash. Closing the splash during startup quits the
   app and stops the half-started backend.

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
checked against; staging warns when what it is given differs, and fails with
`--strict` (as CI runs it). It also pins the SHA-256 of pandoc's Windows zip
and the commit of its source tag, which CI checks. A `LICENSE` in
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
npm run dist                   # toolchain, then the NSIS installer -> desktop/release/LyreTeXt-Setup-<version>.exe
dist/lyretext-backend/lyretext-backend.exe --self-check   # verify the frozen bundle
```

`npm run dist` needs `npm run freeze` first. The version is `version` in
`package.json`; the icon is `assets/icon.ico`, regenerated from
`frontend/assets/logomark.svg` by `node scripts/make-icon.js` (uses a local
Edge or Chrome; the output is committed).

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

`npm run freeze` needs `pyinstaller` in the venv (the version CI pins,
`PYINSTALLER_VERSION` in `.github/workflows/desktop.yml`). The freeze stamps
`lyretext-backend.exe` with a Windows version resource generated from
`package.json` by `backend/winversion.py` (company from `author.name`, product
from `productName`, `version`, and `build.copyright`; description *LyreTeXt
backend*), so a version bump needs no other edit. The numeric version drops
the prerelease (`0.1.0-beta.1` → `0.1.0.0`, with the prerelease flag set;
decision #103). For comparison, electron-builder stamps `LyreTeXt.exe` with
CompanyName *IDEMS International*, ProductName and FileDescription
*LyreTeXt*, the same LegalCopyright, FileVersion `0.1.0-beta.1` (numeric
`0.1.0.1`) and ProductVersion `0.1.0.0`, as logged by CI's *Locate installer*
step. It ends by printing the
SHA-256 of the bootloader it used and of the exe, and fails if any binary in
the bundle is UPX-packed (UPX stays off, #100). A local freeze normally uses
the **stock** bootloader from PyInstaller's wheel, which antivirus heuristics
flag more readily; **release builds come from CI**, which compiles the
bootloader from source. Mock mode replays the small
tracked fixture book in `lyretext/orchestration/mock_fixture` (or the local,
gitignored `demo/` when present), which the freeze bundles as package data, so
any build, including an installed one, can run `LYRETEXT_MOCK=1`.

## CI and releases (#71)

`.github/workflows/desktop.yml` runs on a Windows runner for pull requests that
touch `desktop/`, `lyretext/`, `frontend/`, `tests/` or `requirements.txt`, on
pushes to `main`, and on demand:

1. installs PyInstaller from its source distribution with
   `PYINSTALLER_COMPILE_BOOTLOADER=1`, so the bootloader is compiled on the
   runner with MSVC, and fails unless each compiled bootloader differs from
   the stock wheel's (SHA-256 of both in the log and the job summary); then
   `pytest -q`;
2. downloads pandoc's Windows zip and checks it against the SHA-256 in
   `toolchain/toolchain.json`, fetches pandoc-pretext at the pinned commit, and
   stages them with `stage-toolchain.js --strict`;
3. freezes the backend (plus `--self-check`), checks `lyretext-backend.exe`'s
   version resource against `package.json` and logs its SHA-256, and builds
   the NSIS installer;
4. smoke-tests it (`scripts/ci-smoke.ps1`): silent install; the installed
   backend, driven by `scripts/smoke-backend.js` through the app's own
   `backend-process.js`, must report pandoc and the writer as bundled, take a
   mock run to the manifest gate and leave no process behind on stop; the
   installed app is launched with `LYRETEXT_MOCK=1`, quit, and checked for
   leftover processes; silent uninstall;
5. uploads the installer and the licence/notice files as artifacts.

Pushing a `v<version>` tag (matching `package.json`) also runs the release job,
which creates a **draft** GitHub Release with the installer, the notices, the
exact upstream source tarball of the bundled pandoc and `SHA256SUMS.txt`. A
person reviews and publishes it. Running the workflow by hand with
*release_dry_run* runs that job without creating anything.
