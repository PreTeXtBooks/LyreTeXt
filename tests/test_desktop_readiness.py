"""Backend contracts the desktop app relies on (#69).

* One data dir (``--data-dir`` / LYRETEXT_DATA_DIR) holds config.yml, the runs
  database, output/ and temp/, and the Settings API and run starts agree on
  which config.yml is in force.
* ``GET /api/health`` answers without touching config.yml.
* pandoc, the pretext.lua writer and Rscript are injectable by env var.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from lyretext import __version__
from lyretext.config import (
    BUNDLED_CONFIG_FILE,
    DATA_DIR_ENV,
    RUNS_DB_NAME,
    data_paths,
    get_resolved_config,
)
from lyretext.convert.pandoc import (
    DEFAULT_WRITER,
    PANDOC_EXE_ENV,
    PANDOC_WRITER_ENV,
    find_pandoc,
    resolve_writer,
)
from lyretext.pipeline.rmd import RSCRIPT_EXE_ENV, RmdPipeline


@pytest.fixture
def no_data_dir(monkeypatch):
    # setenv first so monkeypatch records the variable and removes it again at
    # teardown even if the test (e.g. `--data-dir`) sets it.
    monkeypatch.setenv(DATA_DIR_ENV, "")
    monkeypatch.delenv(DATA_DIR_ENV)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "userdata" / "data"
    monkeypatch.setenv(DATA_DIR_ENV, str(d))
    return d


@pytest.fixture
def client():
    from lyretext.api import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Data-dir resolution
# ---------------------------------------------------------------------------

class TestDataPaths:
    def test_default_is_todays_dev_layout(self, no_data_dir):
        paths = data_paths()
        assert paths.root is None
        assert paths.config_file == BUNDLED_CONFIG_FILE
        assert paths.runs_db == Path(RUNS_DB_NAME)
        assert paths.output_dir == Path("output")
        assert paths.temp_dir == Path("temp")
        # Relative paths stay working-directory relative.
        assert paths.resolve("output/run1") == Path("output/run1")

    def test_env_puts_everything_in_the_data_dir(self, data_dir):
        paths = data_paths()
        assert paths.root == data_dir.resolve()
        assert paths.config_file == paths.root / "config.yml"
        assert paths.runs_db == paths.root / RUNS_DB_NAME
        assert paths.output_dir == paths.root / "output"
        assert paths.temp_dir == paths.root / "temp"
        assert paths.resolve("output/run1") == paths.root / "output" / "run1"

    def test_absolute_paths_are_left_alone(self, data_dir, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        assert data_paths().resolve(elsewhere) == elsewhere

    def test_missing_config_is_seeded_from_the_bundled_default(self, data_dir):
        assert not data_dir.exists()
        config_file = data_paths().config_file
        assert data_dir.is_dir()
        assert config_file.read_bytes() == BUNDLED_CONFIG_FILE.read_bytes()

    def test_existing_config_is_never_overwritten(self, data_dir):
        data_dir.mkdir(parents=True)
        (data_dir / "config.yml").write_text("global_options:\n  provider: anthropic\n")
        data_paths()
        assert "anthropic" in (data_dir / "config.yml").read_text()

    def test_data_dir_arg_publishes_the_env_var(self, no_data_dir, tmp_path, monkeypatch):
        from lyretext import __main__ as entry

        target = tmp_path / "from-arg"
        monkeypatch.setattr(sys, "argv", ["lyretext", "--data-dir", str(target)])
        with patch("uvicorn.run") as run:
            entry.main()
        run.assert_called_once()
        assert data_paths().root == target.resolve()

    def test_no_arg_leaves_the_env_alone(self, no_data_dir, monkeypatch):
        import os

        from lyretext import __main__ as entry

        monkeypatch.setattr(sys, "argv", ["lyretext"])
        with patch("uvicorn.run"):
            entry.main()
        assert DATA_DIR_ENV not in os.environ

    def test_sqlite_checkpointer_defaults_into_the_data_dir(self, data_dir):
        pytest.importorskip("langgraph.checkpoint.sqlite")
        from lyretext.orchestration.checkpointing import build_checkpointer

        cp = build_checkpointer(backend="sqlite")
        try:
            assert (data_dir / RUNS_DB_NAME).is_file()
        finally:
            cp.conn.close()


class TestSettingsAndRunsShareOneConfig:
    """The spike's split-brain: Settings edited one config.yml, runs read another."""

    def test_a_settings_edit_is_the_config_the_next_run_uses(self, data_dir, client):
        bundled_before = BUNDLED_CONFIG_FILE.read_bytes()

        resp = client.patch("/api/settings", json={"global_options": {"provider": "anthropic"}})
        assert resp.status_code == 200

        from lyretext import service

        captured = {}

        def fake_invoke(initial_state, *, config_file, runtime_payload, run_id, checkpointer):
            captured["config_file"] = config_file
            captured["state"] = initial_state
            return {}

        with patch.object(service, "invoke_workflow_graph", side_effect=fake_invoke):
            service.start_run("src", "output/r1", "temp/r1", checkpointer=object(), run_id="r1")

        config_file = data_dir.resolve() / "config.yml"
        assert captured["config_file"] == config_file
        assert get_resolved_config(captured["config_file"]).global_options.provider == "anthropic"
        # Relative run dirs land in the data dir too.
        assert captured["state"]["output_dir"] == str(data_dir.resolve() / "output" / "r1")
        assert captured["state"]["temp_dir"] == str(data_dir.resolve() / "temp" / "r1")
        # And nothing was written next to the package.
        assert BUNDLED_CONFIG_FILE.read_bytes() == bundled_before

    def test_dev_mode_run_dirs_are_passed_through_untouched(self, no_data_dir):
        from lyretext import service

        captured = {}

        def fake_invoke(initial_state, **kwargs):
            captured.update(initial_state)
            captured["config_file"] = kwargs["config_file"]
            return {}

        with patch.object(service, "invoke_workflow_graph", side_effect=fake_invoke):
            service.start_run("src", "output/r1", "temp/r1", checkpointer=object(), run_id="r1")

        assert captured["output_dir"] == "output/r1"
        assert captured["temp_dir"] == "temp/r1"
        assert captured["config_file"] == BUNDLED_CONFIG_FILE


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_reports_ok_and_version(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "version": __version__}

    def test_does_not_depend_on_config_yml(self, data_dir, client):
        data_dir.mkdir(parents=True)
        (data_dir / "config.yml").write_text("global_options: [unclosed\n")
        assert client.get("/api/settings").status_code == 500
        assert client.get("/api/health").status_code == 200


# ---------------------------------------------------------------------------
# Tool paths
# ---------------------------------------------------------------------------

class TestToolPaths:
    def test_pandoc_env_override(self, monkeypatch):
        monkeypatch.setenv(PANDOC_EXE_ENV, r"C:\bundled\pandoc.exe")
        monkeypatch.setenv("PANDOC_EXE", r"C:\legacy\pandoc.exe")
        assert find_pandoc() == r"C:\bundled\pandoc.exe"

    def test_pandoc_config_option_beats_env(self, monkeypatch):
        monkeypatch.setenv(PANDOC_EXE_ENV, r"C:\bundled\pandoc.exe")
        assert find_pandoc(r"C:\chosen\pandoc.exe") == r"C:\chosen\pandoc.exe"

    def test_pandoc_legacy_env_still_honoured(self, monkeypatch):
        monkeypatch.delenv(PANDOC_EXE_ENV, raising=False)
        monkeypatch.setenv("PANDOC_EXE", r"C:\legacy\pandoc.exe")
        assert find_pandoc() == r"C:\legacy\pandoc.exe"

    def test_pandoc_falls_back_to_path(self, monkeypatch):
        monkeypatch.delenv(PANDOC_EXE_ENV, raising=False)
        monkeypatch.delenv("PANDOC_EXE", raising=False)
        with patch("lyretext.convert.pandoc.shutil.which", return_value="/usr/bin/pandoc"):
            assert find_pandoc() == "/usr/bin/pandoc"

    def test_writer_default_without_env(self, monkeypatch):
        monkeypatch.delenv(PANDOC_WRITER_ENV, raising=False)
        assert resolve_writer() == DEFAULT_WRITER
        assert resolve_writer(None) == DEFAULT_WRITER

    def test_writer_env_locates_the_default_writer(self, monkeypatch):
        monkeypatch.setenv(PANDOC_WRITER_ENV, r"C:\bundled\pretext.lua")
        assert resolve_writer(DEFAULT_WRITER) == r"C:\bundled\pretext.lua"
        assert resolve_writer(None) == r"C:\bundled\pretext.lua"

    def test_a_deliberately_configured_writer_wins(self, monkeypatch):
        monkeypatch.setenv(PANDOC_WRITER_ENV, r"C:\bundled\pretext.lua")
        assert resolve_writer(r"D:\mine\custom.lua") == r"D:\mine\custom.lua"

    def test_convert_passes_the_resolved_writer(self, tmp_path, monkeypatch):
        from lyretext.convert.pandoc import convert_markdown_to_pretext

        monkeypatch.setenv(PANDOC_WRITER_ENV, r"C:\bundled\pretext.lua")
        src = tmp_path / "ch.md"
        src.write_text("# Hi\n")
        with (
            patch("lyretext.convert.pandoc.find_pandoc", return_value="pandoc"),
            patch("lyretext.convert.pandoc.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            run.return_value.stdout = "<section/>"
            run.return_value.stderr = ""
            convert_markdown_to_pretext(src)
        cmd = run.call_args.args[0]
        assert cmd[cmd.index("-t") + 1] == r"C:\bundled\pretext.lua"

    def test_rscript_precedence(self, monkeypatch):
        monkeypatch.setenv(RSCRIPT_EXE_ENV, r"C:\bundled\Rscript.exe")
        monkeypatch.setenv("RSCRIPT_EXE", r"C:\legacy\Rscript.exe")
        assert RmdPipeline.resolve_rscript() == r"C:\bundled\Rscript.exe"
        assert RmdPipeline.resolve_rscript(r"C:\chosen\Rscript.exe") == r"C:\chosen\Rscript.exe"
        monkeypatch.delenv(RSCRIPT_EXE_ENV)
        assert RmdPipeline.resolve_rscript() == r"C:\legacy\Rscript.exe"

    def test_rscript_falls_back_to_path(self, monkeypatch):
        monkeypatch.delenv(RSCRIPT_EXE_ENV, raising=False)
        monkeypatch.delenv("RSCRIPT_EXE", raising=False)
        with patch("lyretext.pipeline.rmd.shutil.which", return_value="/usr/bin/Rscript"):
            assert RmdPipeline.resolve_rscript() == "/usr/bin/Rscript"
