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
#
# Antivirus false positives (#100): the exe carries a Windows version resource
# generated from desktop/package.json (winversion.py), and UPX stays off. Release
# builds also use a bootloader compiled from source in CI rather than the stock
# one in the PyInstaller wheel (.github/workflows/desktop.yml); local freezes may
# use either.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = Path(SPECPATH)
REPO = HERE.parents[1]

sys.path.insert(0, str(HERE))
from winversion import build_version_info, version_fields  # noqa: E402

VERSION_INFO = build_version_info()
print(f"lyretext-backend version resource: {version_fields()['strings']}")

# UPX must stay off (#100). UPX-packed executables are a classic malware trait
# and among the most reliable antivirus false-positive triggers; it also breaks
# some DLLs. tests/test_desktop_freeze_spec.py fails if this changes.
UPX = False

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
# LYRETEXT_MOCK=1 replays lyretext/orchestration/mock_fixture (#71), which
# LYRETEXT_DATA above already bundles as package data. The gitignored demo/ is
# deliberately not bundled, so every build ships the same tracked content.

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
    version=VERSION_INFO,
    upx=UPX,
)
coll = COLLECT(exe, a.binaries, a.datas, name="lyretext-backend", upx=UPX)
