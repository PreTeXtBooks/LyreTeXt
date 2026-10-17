"""The mock pipeline's tracked fixture (#71).

Mock mode used to replay only the gitignored repo-root demo/, so a clean
checkout, a CI runner or an installed app could not run LYRETEXT_MOCK=1. It now
falls back to lyretext/orchestration/mock_fixture when demo/ is absent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lyretext.orchestration import mock_graph


@pytest.fixture
def no_demo(tmp_path, monkeypatch):
    monkeypatch.setattr(mock_graph, "_DEMO_DIR", tmp_path / "no-demo-here")
    monkeypatch.setattr(mock_graph.time, "sleep", lambda _seconds: None)


def test_fixture_covers_every_mock_chapter():
    for ch in mock_graph._MOCK_CHAPTERS:
        assert (mock_graph._FIXTURE_DIR / "temp_output" / f"{ch['id']}.md").is_file()
        assert (mock_graph._FIXTURE_DIR / "example_output" / f"{ch['id']}.ptx").is_file()


def test_without_demo_the_fixture_is_used(no_demo):
    md_dir, ptx_dir = mock_graph._mock_source_dirs()
    assert md_dir == mock_graph._FIXTURE_DIR / "temp_output"
    assert ptx_dir == mock_graph._FIXTURE_DIR / "example_output"


def test_a_local_demo_still_wins(tmp_path, monkeypatch):
    demo = tmp_path / "demo"
    (demo / "example_output").mkdir(parents=True)
    monkeypatch.setattr(mock_graph, "_DEMO_DIR", demo)
    assert mock_graph._mock_source_dirs() == (demo / "temp_output", demo / "example_output")


def test_mock_nodes_replay_the_fixture(no_demo, tmp_path):
    temp_dir, out_dir = tmp_path / "temp", tmp_path / "output"
    mock_graph._mock_process_to_markdown({"temp_dir": str(temp_dir)})
    manifest = mock_graph._mock_structure_project(
        {"temp_dir": str(temp_dir), "output_dir": str(out_dir)})["manifest"]
    assert len(manifest) == len(mock_graph._MOCK_CHAPTERS)

    for entry in manifest:
        source = Path(entry["source_path"])
        fixture_md = mock_graph._FIXTURE_DIR / "temp_output" / source.name
        assert source.read_text(encoding="utf-8") == fixture_md.read_text(encoding="utf-8")

        result = mock_graph._mock_translate({"output_path": entry["output_path"]})
        fixture_ptx = mock_graph._FIXTURE_DIR / "example_output" / Path(entry["output_path"]).name
        assert result["pretext_output"] == fixture_ptx.read_text(encoding="utf-8")
        assert "No pre-translated file" not in result["pretext_output"]
