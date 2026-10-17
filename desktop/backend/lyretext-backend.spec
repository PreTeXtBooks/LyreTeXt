# PyInstaller spec for the LyreTeXt backend sidecar (#68).
#
# Build with `python desktop/backend/freeze.py` (or `npm run freeze` in
# desktop/), which runs PyInstaller with output in desktop/dist/.
#
# Layout: onedir, not onefile. Onefile unpacks to %TEMP% on every launch
# (slow start, and a bootloader parent + Python child per run); onedir starts
# in-process and is what electron-builder copies into resources/backend.
#
# Data paths: lyretext resolves its read-only assets relative to the package
# (Path(__file__).parent.parent / "frontend", ... / "config.yml"). In a frozen
# onedir build, lyretext's __file__ is <_internal>/lyretext/<mod>.pyc, so
# bundling these at the _internal root keeps every lookup working unchanged.
# The bundled config.yml is only the default: the app runs with --data-dir and
# seeds its own writable copy from it (lyretext.config.data_paths).
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = Path(SPECPATH)
REPO = HERE.parents[1]

PKG = REPO / "lyretext"

# lyretext is not pip-installed, and PyInstaller's collect_* helpers resolve
# packages in an isolated subprocess that sees neither Analysis(pathex=...) nor
# PYTHONPATH: collect_data_files("lyretext") skips with "not a package" and
# collect_submodules("lyretext") silently returns []. So walk the tree directly.
LYRETEXT_MODULES = sorted(
    ".".join(f.relative_to(REPO).with_suffix("").parts).removesuffix(".__init__")
    for f in PKG.rglob("*.py")
    if "__pycache__" not in f.parts
)
LYRETEXT_DATA = [
    (str(f), str(f.parent.relative_to(REPO)))
    for f in PKG.rglob("*")
    if f.is_file() and f.suffix not in (".py", ".pyc") and "__pycache__" not in f.parts
]

datas = [
    (str(REPO / "frontend"), "frontend"),
    (str(REPO / "config.yml"), "."),
]
# Per-stage prompts (lyretext/*/prompts/prompts.md) are package data.
datas += LYRETEXT_DATA
# inferenceswitch reads its model_cards.json through importlib.resources, which
# import analysis does not see. Without it Client() fails on the first real run
# (mock runs never build one); `lyretext-backend --self-check` catches that.
datas += collect_data_files("inferenceswitch")


def _find_demo_dir() -> Path | None:
    """demo/ is gitignored, so a worktree may not have it — look upward."""
    env = os.environ.get("LYRETEXT_DEMO_DIR")
    if env:
        return Path(env)
    for d in [REPO, *REPO.parents]:
        if (d / "demo" / "example_output").is_dir():
            return d / "demo"
    return None


# SPIKE ONLY: LYRETEXT_MOCK=1 replays demo/{temp_output,example_output}
# (mock_graph.py), so bundle them to let the frozen app run a mock pipeline.
# A release build would drop these.
demo = _find_demo_dir()
if demo is not None:
    for sub in ("temp_output", "example_output"):
        if (demo / sub).is_dir():
            datas.append((str(demo / sub), f"demo/{sub}"))
else:
    print("WARNING: demo/ not found; LYRETEXT_MOCK runs will fail in the frozen build")

hiddenimports = (
    # uvicorn.run("lyretext.api:app") imports the app by string.
    LYRETEXT_MODULES
    # uvicorn picks its loop/protocol/lifespan implementations by string
    # ("auto" -> uvicorn.loops.asyncio, uvicorn.protocols.http.h11_impl, ...).
    + collect_submodules("uvicorn")
    # build_checkpointer imports SqliteSaver lazily inside a function.
    + collect_submodules("langgraph.checkpoint.sqlite")
    # inferenceswitch imports provider SDKs by name at adapter build time
    # (adapters/base.py require_sdk). lyretext.config imports anthropic and
    # google.genai statically today, but keep the bundle correct even if that
    # changes; the adapters themselves are listed for the same reason.
    + collect_submodules("inferenceswitch")
    + ["anthropic", "google.genai", "google.genai.types"]
    # lyretext.tls imports truststore inside a function, and truststore picks
    # its platform backend (_windows / _macos / _openssl) at import time (#90).
    + collect_submodules("truststore")
)

a = Analysis(
    [str(HERE / "entry.py")],
    pathex=[str(REPO)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        # Dev/test only; pulled in transitively and large.
        "pytest",
        "IPython",
        "matplotlib",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="lyretext-backend",
    # Console subsystem: stdin/stdout must be real handles for the parent
    # watchdog and the log redirect. Electron spawns it with windowsHide, so no
    # console window appears.
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="lyretext-backend", upx=False)
