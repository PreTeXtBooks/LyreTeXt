"""Antivirus hardening of the frozen backend (#100).

* lyretext-backend.exe's Windows version resource is generated from
  desktop/package.json, so it matches the installer and LyreTeXt.exe.
* UPX stays off in the PyInstaller spec.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "desktop" / "backend"
SPEC = BACKEND / "lyretext-backend.spec"
PACKAGE_JSON = REPO / "desktop" / "package.json"


def _winversion():
    spec = importlib.util.spec_from_file_location("winversion", BACKEND / "winversion.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.1.0-beta.1", (0, 1, 0, 0)),
        ("0.1.0", (0, 1, 0, 0)),
        ("1.2.3+build.7", (1, 2, 3, 0)),
        ("12.0.4-rc.2", (12, 0, 4, 0)),
    ],
)
def test_numeric_version_drops_prerelease(version, expected):
    assert _winversion().numeric_version(version) == expected


@pytest.mark.parametrize("version", ["0.1", "v0.1.0", "0.1.0.0", "70000.0.0"])
def test_numeric_version_rejects_non_windows_versions(version):
    with pytest.raises(ValueError):
        _winversion().numeric_version(version)


def test_version_fields_follow_package_json():
    pkg = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    f = _winversion().version_fields(PACKAGE_JSON)
    s = f["strings"]
    assert s["CompanyName"] == pkg["author"]["name"]
    assert s["ProductName"] == pkg["productName"] == pkg["build"]["productName"]
    assert s["FileDescription"] == "LyreTeXt backend"
    assert s["FileVersion"] == s["ProductVersion"] == pkg["version"]
    assert s["LegalCopyright"] == pkg["build"]["copyright"]
    assert s["OriginalFilename"] == "lyretext-backend.exe"
    assert f["prerelease"] == ("-" in pkg["version"])


def test_version_fields_from_another_package(tmp_path):
    pkg = {
        "version": "2.3.4",
        "productName": "LyreTeXt",
        "author": "Someone Else <a@example.org>",
        "build": {"productName": "LyreTeXt", "copyright": "Copyright (c) 2030"},
    }
    p = tmp_path / "package.json"
    p.write_text(json.dumps(pkg), encoding="utf-8")
    f = _winversion().version_fields(p)
    assert f["numeric"] == (2, 3, 4, 0)
    assert f["prerelease"] is False
    assert f["strings"]["CompanyName"] == "Someone Else"
    assert f["strings"]["FileVersion"] == "2.3.4"


def test_build_version_info_round_trips():
    pytest.importorskip("PyInstaller")
    wv = _winversion()
    text = str(wv.build_version_info(PACKAGE_JSON))
    pkg = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    numeric = wv.version_fields(PACKAGE_JSON)["numeric"]
    assert f"filevers={numeric}" in text and f"prodvers={numeric}" in text
    for value in ("LyreTeXt backend", pkg["author"]["name"], pkg["version"], "lyretext-backend.exe"):
        assert value in text


def test_upx_stays_off():
    src = SPEC.read_text(encoding="utf-8")
    assert re.search(r"^UPX = False\b", src, re.M), "the spec must define UPX = False"
    assert not re.search(r"upx\s*=\s*True", src), "UPX must stay off (#100)"
    # Every upx= argument (EXE and COLLECT) goes through the UPX constant.
    upx_args = re.findall(r"\bupx\s*=\s*(\w+)", src)
    assert len(upx_args) >= 2 and set(upx_args) == {"UPX"}, upx_args


def test_spec_embeds_the_version_resource():
    src = SPEC.read_text(encoding="utf-8")
    assert "version=VERSION_INFO" in src
    assert "build_version_info()" in src
