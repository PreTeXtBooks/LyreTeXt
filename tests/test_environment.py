"""Environment checks and the bundled toolchain (#70, decision #65).

* R / knitr detection: version and knitr from one probe, with a timeout that
  kills a hung R, and actionable guidance (CRAN, install.packages("knitr"))
  whenever something is missing.
* An R Markdown run without R or knitr is refused up front (422) with that
  guidance, and the pipeline itself fails with it rather than a subprocess
  error.
* pandoc / pretext.lua detection, including "bundled" when the tool lives in
  LYRETEXT_TOOLCHAIN_DIR.
* GET /api/environment reports all of it plus API keys set/unset — never a
  key value — behind the desktop auth middleware.
* The Electron shell points the backend at the bundled copies only when
  packaged.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from lyretext import environment
from lyretext.config import AUTH_TOKEN_ENV, DATA_DIR_ENV, PROVIDER_KEY_ENV
from lyretext.convert.pandoc import PANDOC_EXE_ENV, PANDOC_WRITER_ENV, PandocNotFoundError
from lyretext.environment import (
    TOOLCHAIN_DIR_ENV,
    detect_pandoc,
    detect_r,
    detect_writer,
    rmd_preflight,
    run_needs_r,
    run_probe,
)
from lyretext.pipeline.rmd import (
    KNITR_INSTALL_CMD,
    RSCRIPT_EXE_ENV,
    RmdPipeline,
    RToolchainError,
    r_download_url,
)
from lyretext.security import AUTH_HEADER

REPO = Path(__file__).resolve().parents[1]
TOKEN = "t0ken-" + "b" * 58
BASE = "http://127.0.0.1:8124"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in [
        AUTH_TOKEN_ENV, DATA_DIR_ENV, TOOLCHAIN_DIR_ENV, PANDOC_EXE_ENV, "PANDOC_EXE",
        PANDOC_WRITER_ENV, RSCRIPT_EXE_ENV, "RSCRIPT_EXE", "LYRETEXT_MOCK",
        *PROVIDER_KEY_ENV.values(),
    ]:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    # Config resolution loads the developer's .env in dev mode, which would
    # bring real keys back in.
    monkeypatch.setattr("lyretext.config._load_dotenv", lambda *a, **kw: None)


@pytest.fixture
def client():
    from lyretext.api import app

    with TestClient(app, base_url=BASE) as c:
        yield c


def _done(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


R_OK = _done("R_VERSION=4.6.1\nKNITR=1.51\n")
R_NO_KNITR = _done("R_VERSION=4.6.1\nKNITR=\n")


# ---------------------------------------------------------------------------
# run_probe: timeouts really kill
# ---------------------------------------------------------------------------

class TestRunProbe:
    def test_captures_output(self):
        proc = run_probe([sys.executable, "-c", "print('hi')"], timeout=30)
        assert proc.returncode == 0
        assert proc.stdout.strip() == "hi"

    def test_timeout_kills_the_process(self):
        start = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            run_probe([sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.5)
        # Well short of the 60s the child wanted: it was killed, not waited for.
        assert time.monotonic() - start < 20

    def test_timeout_kills_the_tree(self):
        popen = MagicMock()
        popen.pid = 4242
        popen.communicate.side_effect = [subprocess.TimeoutExpired("r", 1), ("", "")]
        with (
            patch("lyretext.environment.subprocess.Popen", return_value=popen),
            patch("lyretext.environment._kill_tree") as kill,
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                run_probe(["Rscript", "-e", "1"], timeout=1)
        kill.assert_called_once_with(popen)

    def test_missing_executable_is_oserror(self, tmp_path):
        with pytest.raises(OSError):
            run_probe([str(tmp_path / "nope.exe")], timeout=5)


# ---------------------------------------------------------------------------
# R + knitr detection
# ---------------------------------------------------------------------------

@pytest.fixture
def rscript(monkeypatch, tmp_path):
    path = tmp_path / "Rscript.exe"
    monkeypatch.setenv(RSCRIPT_EXE_ENV, str(path))
    return str(path)


class TestDetectR:
    def test_version_and_knitr(self, rscript):
        with patch("lyretext.environment.run_probe", return_value=R_OK) as probe:
            r, knitr = detect_r()
        assert r == {"ok": True, "error": None, "guidance": None, "path": rscript,
                     "source": RSCRIPT_EXE_ENV, "version": "4.6.1"}
        assert knitr["ok"] and knitr["version"] == "1.51"
        cmd = probe.call_args.args[0]
        assert cmd[:2] == [rscript, "-e"] and "requireNamespace('knitr'" in cmd[2]

    def test_one_probe_has_a_timeout(self, rscript):
        with patch("lyretext.environment.run_probe", return_value=R_OK) as probe:
            detect_r(timeout=7)
        assert probe.call_count == 1
        assert probe.call_args.args[1] == 7

    def test_knitr_missing(self, rscript):
        with patch("lyretext.environment.run_probe", return_value=R_NO_KNITR):
            r, knitr = detect_r()
        assert r["ok"]
        assert not knitr["ok"]
        assert KNITR_INSTALL_CMD in knitr["guidance"]

    def test_r_not_found_anywhere(self):
        with patch.object(RmdPipeline, "_find_rscript", side_effect=FileNotFoundError("x")):
            r, knitr = detect_r()
        assert not r["ok"] and r["path"] is None
        assert r_download_url() in r["guidance"]
        assert KNITR_INSTALL_CMD in r["guidance"]
        assert not knitr["ok"] and "Not checked" in knitr["error"]

    def test_configured_path_missing(self, rscript):
        with patch("lyretext.environment.run_probe", side_effect=FileNotFoundError(2, "no")):
            r, _ = detect_r()
        assert not r["ok"]
        assert rscript in r["error"] and RSCRIPT_EXE_ENV in r["error"]
        assert r_download_url() in r["guidance"]

    def test_hung_r_times_out(self, rscript):
        with patch("lyretext.environment.run_probe",
                   side_effect=subprocess.TimeoutExpired("Rscript", 20)):
            r, knitr = detect_r(timeout=20)
        assert not r["ok"] and "did not respond within 20s" in r["error"]
        assert not knitr["ok"]

    def test_r_errors(self, rscript):
        with patch("lyretext.environment.run_probe",
                   return_value=_done("", "Fatal error: cannot open file", 2)):
            r, _ = detect_r()
        assert not r["ok"] and "cannot open file" in r["error"]

    @pytest.mark.skipif(sys.platform != "win32", reason="windows quoting")
    def test_probe_expression_survives_windows_quoting(self):
        # No double quotes, so list2cmdline leaves the expression intact.
        assert '"' not in environment.R_PROBE


class TestRmdPreflight:
    def test_passes(self, rscript):
        with patch("lyretext.environment.run_probe", return_value=R_OK):
            rmd_preflight()

    def test_no_r(self):
        with patch.object(RmdPipeline, "_find_rscript", side_effect=FileNotFoundError("x")):
            with pytest.raises(RToolchainError) as exc:
                rmd_preflight()
        msg = str(exc.value)
        assert "R Markdown projects need R" in msg
        assert r_download_url() in msg and KNITR_INSTALL_CMD in msg

    def test_no_knitr(self, rscript):
        with patch("lyretext.environment.run_probe", return_value=R_NO_KNITR):
            with pytest.raises(RToolchainError) as exc:
                rmd_preflight()
        assert "knitr package is not" in str(exc.value)
        assert KNITR_INSTALL_CMD in str(exc.value)


# ---------------------------------------------------------------------------
# The pipeline's own guard: actionable, not a subprocess error
# ---------------------------------------------------------------------------

@pytest.fixture
def rmd_project(tmp_path):
    src = tmp_path / "book"
    src.mkdir()
    (src / "ch1.Rmd").write_text("# One\n", encoding="utf-8")
    return src


class TestRmdPipelineGuard:
    def test_no_r_anywhere(self, rmd_project, tmp_path):
        with patch.object(RmdPipeline, "_find_rscript", side_effect=FileNotFoundError("x")):
            with pytest.raises(RToolchainError) as exc:
                RmdPipeline().compile_to_markdown(rmd_project, temp_dir=tmp_path / "t")
        assert r_download_url() in str(exc.value)

    def test_configured_path_missing(self, rmd_project, tmp_path, rscript):
        with patch("lyretext.pipeline.rmd.subprocess.run") as run:
            with pytest.raises(RToolchainError) as exc:
                RmdPipeline().compile_to_markdown(rmd_project, temp_dir=tmp_path / "t")
        run.assert_not_called()
        assert f"Rscript was not found at {rscript}" in str(exc.value)

    def test_knitr_missing_stops_the_run(self, rmd_project, tmp_path):
        stderr = "Error in loadNamespace(x) : there is no package called \u2018knitr\u2019"
        with (
            patch.object(RmdPipeline, "resolve_rscript", return_value=sys.executable),
            patch("lyretext.pipeline.rmd.subprocess.run", return_value=_done("", stderr, 1)),
        ):
            with pytest.raises(RToolchainError) as exc:
                RmdPipeline().compile_to_markdown(rmd_project, temp_dir=tmp_path / "t")
        assert KNITR_INSTALL_CMD in str(exc.value)

    def test_other_knit_errors_stay_per_file(self, rmd_project, tmp_path):
        with (
            patch.object(RmdPipeline, "resolve_rscript", return_value=sys.executable),
            patch("lyretext.pipeline.rmd.subprocess.run",
                  return_value=_done("", "Error: object 'x' not found", 1)),
        ):
            result = RmdPipeline().compile_to_markdown(rmd_project, temp_dir=tmp_path / "t")
        assert result["errors"] and "object 'x' not found" in result["errors"][0]


class TestRunNeedsR:
    def test_rmd_project(self, rmd_project):
        assert run_needs_r(rmd_project, "rmd")

    def test_tex_project_configured_as_rmd(self, tmp_path):
        (tmp_path / "main.tex").write_text("\\documentclass{article}", encoding="utf-8")
        assert not run_needs_r(tmp_path, "rmd")

    def test_empty_or_missing_source(self, tmp_path):
        assert not run_needs_r(tmp_path, "rmd")
        assert not run_needs_r(tmp_path / "nope", "rmd")


# ---------------------------------------------------------------------------
# pandoc + writer detection
# ---------------------------------------------------------------------------

PANDOC_VERSION_OUT = (
    "pandoc 3.10.1\nFeatures: +server +lua\nScripting engine: Lua 5.4\n"
    "User data directory: {data}\nCopyright (C) 2006-2024 John MacFarlane.\n"
)


class TestDetectPandoc:
    def test_on_path(self, tmp_path):
        with (
            patch("lyretext.environment.locate_pandoc", return_value=("/usr/bin/pandoc", "PATH")),
            patch("lyretext.environment.run_probe",
                  return_value=_done(PANDOC_VERSION_OUT.format(data=tmp_path))),
        ):
            p = detect_pandoc()
        assert p["ok"] and p["version"] == "3.10.1" and p["source"] == "PATH"
        assert not p["bundled"] and p["data_dir"] == str(tmp_path)

    def test_bundled(self, tmp_path, monkeypatch):
        exe = tmp_path / "toolchain" / "pandoc" / "pandoc.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        monkeypatch.setenv(TOOLCHAIN_DIR_ENV, str(tmp_path / "toolchain"))
        monkeypatch.setenv(PANDOC_EXE_ENV, str(exe))
        with patch("lyretext.environment.run_probe",
                   return_value=_done(PANDOC_VERSION_OUT.format(data=tmp_path))):
            p = detect_pandoc()
        assert p["ok"] and p["bundled"] and p["source"] == "bundled"

    def test_not_found(self):
        with patch("lyretext.environment.locate_pandoc", side_effect=PandocNotFoundError("x")):
            p = detect_pandoc()
        assert not p["ok"] and "pandoc.org" in p["guidance"]

    def test_too_old(self):
        with (
            patch("lyretext.environment.locate_pandoc", return_value=("pandoc", "PATH")),
            patch("lyretext.environment.run_probe", return_value=_done("pandoc 2.19.2\n")),
        ):
            p = detect_pandoc()
        assert not p["ok"] and "too old" in p["error"]

    def test_hung(self):
        with (
            patch("lyretext.environment.locate_pandoc", return_value=("pandoc", "PATH")),
            patch("lyretext.environment.run_probe",
                  side_effect=subprocess.TimeoutExpired("pandoc", 15)),
        ):
            p = detect_pandoc()
        assert not p["ok"] and "did not respond" in p["error"]


PANDOC_OK = {"ok": True, "path": "pandoc", "data_dir": None}


class TestDetectWriter:
    def test_found_in_user_data_dir_and_converts(self, tmp_path):
        (tmp_path / "custom").mkdir()
        lua = tmp_path / "custom" / "pretext.lua"
        lua.write_text("-- writer", encoding="utf-8")
        pandoc = {**PANDOC_OK, "data_dir": str(tmp_path)}
        with patch("lyretext.environment.convert_markdown_to_pretext",
                   return_value=("<section><title>x</title></section>", [])) as conv:
            w = detect_writer(pandoc)
        assert w["ok"] and w["path"] == str(lua)
        assert w["source"] == "pandoc user data directory"
        # The check is a real conversion of a .tex fixture with the found writer.
        assert Path(conv.call_args.args[0]).suffix == ".tex"
        assert conv.call_args.kwargs["writer"] == str(lua)

    def test_bundled_via_env(self, tmp_path, monkeypatch):
        lua = tmp_path / "toolchain" / "pretext-writer" / "pretext.lua"
        lua.parent.mkdir(parents=True)
        lua.write_text("-- writer", encoding="utf-8")
        monkeypatch.setenv(TOOLCHAIN_DIR_ENV, str(tmp_path / "toolchain"))
        monkeypatch.setenv(PANDOC_WRITER_ENV, str(lua))
        with patch("lyretext.environment.convert_markdown_to_pretext",
                   return_value=("<section/>", [])):
            w = detect_writer(PANDOC_OK)
        assert w["ok"] and w["bundled"] and w["source"] == "bundled"

    def test_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        w = detect_writer({**PANDOC_OK, "data_dir": str(tmp_path)})
        assert not w["ok"] and "not found" in w["error"]
        assert "pandoc-pretext" in w["guidance"]

    def test_conversion_fails(self, tmp_path):
        lua = tmp_path / "pretext.lua"
        lua.write_text("error('boom')", encoding="utf-8")
        with patch("lyretext.environment.convert_markdown_to_pretext",
                   return_value=("", ["pandoc conversion failed for check.tex: boom"])):
            w = detect_writer(PANDOC_OK, str(lua))
        assert not w["ok"] and "boom" in w["error"]

    def test_real_conversion_when_available(self):
        p = detect_pandoc()
        if not p["ok"]:
            pytest.skip("pandoc not installed")
        w = detect_writer(p)
        if w["path"] is None:
            pytest.skip("pretext.lua not installed")
        assert w["ok"], w


# ---------------------------------------------------------------------------
# GET /api/environment
# ---------------------------------------------------------------------------

FAKE_REPORT = {
    "pandoc": {"ok": True, "error": None, "guidance": None, "path": "p", "source": "bundled",
               "bundled": True, "version": "3.10.1", "data_dir": None},
    "writer": {"ok": True, "error": None, "guidance": None, "path": "w", "source": "bundled",
               "bundled": True},
    "r": {"ok": False, "error": "R was not found on this computer.", "guidance": "Install R",
          "path": None, "source": None, "version": None},
    "knitr": {"ok": False, "error": "Not checked: R is not available.", "guidance": None,
              "version": None},
    "toolchain_dir": "t",
}


class TestEnvironmentEndpoint:
    def test_report_and_key_status_without_values(self, client, monkeypatch):
        secret = "secret-gemini-value-0123456789"
        monkeypatch.setenv(PROVIDER_KEY_ENV["gemini"], secret)
        with patch("lyretext.api.environment_report", return_value=dict(FAKE_REPORT)):
            r = client.get("/api/environment")
        assert r.status_code == 200
        body = r.json()
        assert body["pandoc"]["source"] == "bundled"
        assert body["r"]["ok"] is False
        keys = {k["provider"]: k["set"] for k in body["api_keys"]}
        assert keys == {"gemini": True, "anthropic": False}
        assert secret not in r.text
        assert body["desktop"] is False

    def test_unreadable_config_still_reports(self, client):
        with (
            patch("lyretext.api._config.get_resolved_config", side_effect=ValueError("bad yaml")),
            patch("lyretext.api.environment_report", return_value=dict(FAKE_REPORT)) as rep,
        ):
            r = client.get("/api/environment")
        assert r.status_code == 200
        assert rep.call_args.kwargs == {"pandoc_exe": None, "pandoc_writer": "pretext.lua"}

    def test_behind_desktop_auth(self, client, monkeypatch):
        monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)
        with patch("lyretext.api.environment_report", return_value=dict(FAKE_REPORT)):
            assert client.get("/api/environment").status_code == 401
            r = client.get("/api/environment", headers={AUTH_HEADER: TOKEN})
        assert r.status_code == 200 and r.json()["desktop"] is True


# ---------------------------------------------------------------------------
# Starting an R Markdown run without R: 422 with the fix
# ---------------------------------------------------------------------------

def _no_r():
    return (
        {"ok": False, "error": "R was not found on this computer.",
         "guidance": environment.r_missing_guidance(), "path": None, "source": None,
         "version": None},
        {"ok": False, "error": "Not checked", "guidance": None, "version": None},
    )


class TestRunStartPreflight:
    def test_rmd_without_r_is_refused_with_guidance(self, client, rmd_project):
        with (
            patch("lyretext.environment.detect_r", return_value=_no_r()),
            patch("lyretext.api.start_run") as start,
        ):
            r = client.post("/api/runs", json={"source": str(rmd_project)})
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert r_download_url() in detail and KNITR_INSTALL_CMD in detail
        start.assert_not_called()

    def test_rmd_without_knitr_is_refused(self, client, rmd_project):
        r_ok = ({"ok": True, "error": None, "guidance": None, "path": "R", "source": "PATH",
                 "version": "4.6.1"},
                {"ok": False, "error": "x", "guidance": "y", "version": None})
        with patch("lyretext.environment.detect_r", return_value=r_ok):
            r = client.post("/api/runs", json={"source": str(rmd_project)})
        assert r.status_code == 422 and KNITR_INSTALL_CMD in r.json()["detail"]

    def test_rmd_with_r_starts(self, client, rmd_project):
        r_ok = ({"ok": True, "error": None, "guidance": None, "path": "R", "source": "PATH",
                 "version": "4.6.1"},
                {"ok": True, "error": None, "guidance": None, "version": "1.51"})
        with (
            patch("lyretext.environment.detect_r", return_value=r_ok),
            patch("lyretext.api.start_run"),
        ):
            r = client.post("/api/runs", json={"source": str(rmd_project)})
        assert r.status_code == 202

    def test_latex_never_checks_r(self, client, tmp_path):
        (tmp_path / "main.tex").write_text("\\documentclass{article}", encoding="utf-8")
        with (
            patch("lyretext.environment.detect_r") as detect,
            patch("lyretext.api.start_run"),
        ):
            r = client.post("/api/runs", json={"source": str(tmp_path)})
        assert r.status_code == 202
        detect.assert_not_called()

    def test_mock_mode_skips(self, client, rmd_project, monkeypatch):
        monkeypatch.setenv("LYRETEXT_MOCK", "1")
        with (
            patch("lyretext.environment.detect_r") as detect,
            patch("lyretext.api.start_run"),
        ):
            r = client.post("/api/runs", json={"source": str(rmd_project)})
        assert r.status_code == 202
        detect.assert_not_called()

    def test_upload_refused_and_staging_removed(self, client):
        staged = []
        real_mkdtemp = __import__("tempfile").mkdtemp

        def mkdtemp(*a, **kw):
            staged.append(real_mkdtemp(*a, **kw))
            return staged[-1]

        with (
            patch("lyretext.environment.detect_r", return_value=_no_r()),
            patch("lyretext.api.tempfile.mkdtemp", side_effect=mkdtemp),
            patch("lyretext.api.start_run") as start,
        ):
            r = client.post(
                "/api/runs/upload",
                files=[("files", ("ch1.Rmd", b"# One\n", "text/plain"))],
                data={"paths": ["book/ch1.Rmd"]},
            )
        assert r.status_code == 422 and KNITR_INSTALL_CMD in r.json()["detail"]
        start.assert_not_called()
        assert staged and not Path(staged[0]).exists()


# ---------------------------------------------------------------------------
# Electron: bundled toolchain env, packaged only
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestElectronToolchainEnv:
    def _env(self, opts, parent=None):
        script = (
            "const { toolchainEnv } = require(process.argv[1]);"
            "const [opts, parent] = JSON.parse(process.argv[2]);"
            "process.stdout.write(JSON.stringify(toolchainEnv(opts, parent)));"
        )
        out = subprocess.run(
            ["node", "-e", script, str(REPO / "desktop" / "backend-process.js"),
             json.dumps([opts, parent or {}])],
            capture_output=True, text=True, timeout=60, check=True,
        )
        return json.loads(out.stdout)

    @pytest.fixture
    def resources(self, tmp_path):
        exe = "pandoc.exe" if sys.platform == "win32" else "pandoc"
        (tmp_path / "toolchain" / "pandoc").mkdir(parents=True)
        (tmp_path / "toolchain" / "pandoc" / exe).write_bytes(b"")
        (tmp_path / "toolchain" / "pretext-writer").mkdir()
        (tmp_path / "toolchain" / "pretext-writer" / "pretext.lua").write_text("--")
        return tmp_path

    def test_packaged_points_at_bundle(self, resources):
        env = self._env({"isPackaged": True, "resourcesPath": str(resources)})
        tc = resources / "toolchain"
        assert Path(env["LYRETEXT_TOOLCHAIN_DIR"]) == tc
        assert Path(env["LYRETEXT_PANDOC_EXE"]).parent == tc / "pandoc"
        assert Path(env["LYRETEXT_PANDOC_WRITER"]) == tc / "pretext-writer" / "pretext.lua"

    def test_dev_leaves_path_lookup(self, resources):
        assert self._env({"isPackaged": False, "resourcesPath": str(resources)}) == {}

    def test_explicit_override_wins(self, resources):
        env = self._env({"isPackaged": True, "resourcesPath": str(resources)},
                        {"LYRETEXT_PANDOC_EXE": "C:\\mine\\pandoc.exe"})
        assert "LYRETEXT_PANDOC_EXE" not in env
        assert "LYRETEXT_PANDOC_WRITER" in env

    def test_no_bundle(self, tmp_path):
        assert self._env({"isPackaged": True, "resourcesPath": str(tmp_path)}) == {}
