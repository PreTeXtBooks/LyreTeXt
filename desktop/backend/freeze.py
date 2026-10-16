"""Freeze the LyreTeXt backend with PyInstaller into desktop/dist/lyretext-backend.

Run with the project venv's python (PyInstaller must be installed there):
    python desktop/backend/freeze.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import PyInstaller.__main__

HERE = Path(__file__).resolve().parent
DESKTOP = HERE.parent

PyInstaller.__main__.run([
    str(HERE / "lyretext-backend.spec"),
    "--noconfirm",
    "--clean",
    "--distpath", str(DESKTOP / "dist"),
    "--workpath", str(DESKTOP / "build"),
    *sys.argv[1:],
])
