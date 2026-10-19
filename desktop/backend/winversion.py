"""Windows version resource for lyretext-backend.exe, derived from desktop/package.json (#100).

An executable with no VERSIONINFO (no company, product or description) scores
worse with antivirus reputation heuristics, so the frozen backend carries the
same metadata as the installer and LyreTeXt.exe, which electron-builder stamps
from package.json. Deriving it here, at freeze time, keeps the two in sync with
nothing to edit by hand when the version changes.

`version_fields()` is plain data (and what the tests check); `build_version_info()`
turns it into the PyInstaller structure the spec passes as `EXE(version=...)`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PACKAGE_JSON = Path(__file__).resolve().parents[1] / "package.json"

EXE_NAME = "lyretext-backend"
FILE_DESCRIPTION = "LyreTeXt backend"

# VS_FIXEDFILEINFO.dwFileFlags: VS_FF_PRERELEASE.
VS_FF_PRERELEASE = 0x2

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def numeric_version(version: str) -> tuple[int, int, int, int]:
    """The four-part numeric Windows version for a semver string.

    The prerelease tag is dropped (0.1.0-beta.1 -> 0.1.0.0), as electron-builder
    does for LyreTeXt.exe: a fourth field taken from the prerelease would make
    0.1.0-beta.2 (0.1.0.2) sort above the final 0.1.0 (0.1.0.0). The full
    version stays readable in the FileVersion/ProductVersion strings.
    """
    m = _SEMVER.match(version)
    if not m:
        raise ValueError(f"package.json version {version!r} is not semver (major.minor.patch[-pre])")
    parts = tuple(int(g) for g in m.groups()[:3])
    if any(p > 0xFFFF for p in parts):
        raise ValueError(f"version {version!r}: Windows version fields are 16-bit (max 65535)")
    return (*parts, 0)


def version_fields(package_json: Path = PACKAGE_JSON) -> dict:
    """The version resource's contents, from package.json."""
    pkg = json.loads(Path(package_json).read_text(encoding="utf-8"))
    version = pkg["version"]
    author = pkg["author"]
    company = author["name"] if isinstance(author, dict) else re.sub(r"\s*[<(].*$", "", author)
    product = pkg.get("productName") or pkg["build"]["productName"]
    return {
        "numeric": numeric_version(version),
        "prerelease": "-" in version.split("+")[0],
        "strings": {
            "CompanyName": company,
            "FileDescription": FILE_DESCRIPTION,
            "FileVersion": version,
            "InternalName": EXE_NAME,
            "LegalCopyright": pkg["build"]["copyright"],
            "OriginalFilename": f"{EXE_NAME}.exe",
            "ProductName": product,
            "ProductVersion": version,
        },
    }


def build_version_info(package_json: Path = PACKAGE_JSON):
    """A PyInstaller VSVersionInfo for `EXE(version=...)`. Needs PyInstaller."""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    f = version_fields(package_json)
    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=f["numeric"],
            prodvers=f["numeric"],
            mask=0x3F,
            flags=VS_FF_PRERELEASE if f["prerelease"] else 0,
            OS=0x40004,  # VOS_NT_WINDOWS32
            fileType=0x1,  # VFT_APP
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            # 0409 = U.S. English, 04B0 = Unicode (UTF-16): the usual pair.
            StringFileInfo([StringTable("040904B0", [StringStruct(k, v) for k, v in f["strings"].items()])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


if __name__ == "__main__":
    print(json.dumps(version_fields(), indent=2, ensure_ascii=False))
