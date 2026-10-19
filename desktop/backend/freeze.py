"""Freeze the LyreTeXt backend with PyInstaller into desktop/dist/lyretext-backend.

Run with the project venv's python (PyInstaller must be installed there):
    python desktop/backend/freeze.py

The spec stamps lyretext-backend.exe with a Windows version resource generated
from desktop/package.json (winversion.py), so there is nothing to keep in sync
by hand. Release builds come from CI, which compiles PyInstaller's bootloader
from source (#100); a local freeze uses whichever bootloader the venv's
PyInstaller has, normally the stock one from the wheel. The SHA-256 of both is
printed at the end so the two can be told apart.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import PyInstaller
import PyInstaller.__main__

HERE = Path(__file__).resolve().parent
DESKTOP = HERE.parent
EXE = DESKTOP / "dist" / "lyretext-backend" / "lyretext-backend.exe"
# Console, non-debug bootloader: what the spec (console=True) builds on.
BOOTLOADER = Path(PyInstaller.__file__).parent / "bootloader" / "Windows-64bit-intel" / "run.exe"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


PyInstaller.__main__.run([
    str(HERE / "lyretext-backend.spec"),
    "--noconfirm",
    "--clean",
    "--distpath", str(DESKTOP / "dist"),
    "--workpath", str(DESKTOP / "build"),
    *sys.argv[1:],
])

for label, path in (("bootloader", BOOTLOADER), ("frozen exe", EXE)):
    if path.is_file():
        print(f"{label:<11}{sha256(path)}  {path}")


def upx_packed(bundle: Path) -> list[Path]:
    """Binaries in the bundle with UPX sections (UPX0/UPX1/...)."""
    import pefile  # a PyInstaller dependency on Windows

    packed = []
    for f in sorted(bundle.rglob("*")):
        if f.suffix.lower() in (".exe", ".dll", ".pyd") and f.is_file():
            pe = pefile.PE(str(f), fast_load=True)
            if any(s.Name.rstrip(b"\0").upper().startswith(b"UPX") for s in pe.sections):
                packed.append(f)
            pe.close()
    return packed


# UPX stays off (#100): packed binaries are a strong antivirus trigger. The spec
# never asks for it; this catches a packed binary arriving any other way.
if sys.platform == "win32" and EXE.parent.is_dir():
    packed = upx_packed(EXE.parent)
    if packed:
        sys.exit("UPX-packed binaries in the frozen backend (#100):\n  " + "\n  ".join(map(str, packed)))
    print("no UPX-packed binaries in the bundle")
