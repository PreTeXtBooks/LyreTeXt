"""Tests for the LaTeX (.tex) pipeline and the PreTeXt splitter."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from lyretext.pipeline.tex import TexPipeline
from lyretext.convert.split import split_pretext_by_section
from lyretext.read.graph import build_skeleton_graph


def _skeleton_run(project_dir: Path, tmp_root: Path, pipeline: str) -> dict:
    """Run the read subgraph over *project_dir* with *pipeline* configured."""
    return build_skeleton_graph().invoke(
        {
            "project_source": str(project_dir),
            "temp_dir": str(tmp_root / "temp"),
            "output_dir": str(tmp_root / "out"),
        },
        {"configurable": {"runtime_options": {
            "global_options": {"pipeline": pipeline, "execution_mode": "direct"},
            "node_overrides": {},
        }}},
    )


@pytest.fixture
def tmp_project():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def test_identifies_main_file(tmp_project):
    main = tmp_project / "book.tex"
    main.write_text(r"\documentclass{book}" "\n\\begin{document}\n\\end{document}\n", encoding="utf-8")

    pipeline = TexPipeline()
    config = pipeline.detect_and_resolve_config(tmp_project)

    assert config["main_file"] == str(main)


def test_parses_includes(tmp_project):
    main = tmp_project / "book.tex"
    main.write_text(
        "\\documentclass{book}\n"
        "\\begin{document}\n"
        "\\include{ch1}\n"
        "\\include{ch2}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    (tmp_project / "ch1.tex").write_text("chapter one", encoding="utf-8")
    (tmp_project / "ch2.tex").write_text("chapter two", encoding="utf-8")

    pipeline = TexPipeline()
    output_dir = tmp_project / "out"
    result = pipeline.compile_to_markdown(tmp_project, output_dir=output_dir, temp_dir=tmp_project / "tmp")

    assert result["errors"] == []
    assert set(result["markdown_files"].keys()) == {"ch1", "ch2"}
    assert result["main_file"] == str(main)
    assert result["project_root"] == str(tmp_project)
    assert Path(result["markdown_files"]["ch1"]).exists()
    assert Path(result["markdown_files"]["ch2"]).exists()


def test_skips_commented_includes(tmp_project):
    main = tmp_project / "book.tex"
    main.write_text(
        "\\documentclass{book}\n"
        "\\begin{document}\n"
        "\\include{ch1}\n"
        "%\\include{ch3}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    (tmp_project / "ch1.tex").write_text("chapter one", encoding="utf-8")
    (tmp_project / "ch3.tex").write_text("chapter three", encoding="utf-8")

    pipeline = TexPipeline()
    output_dir = tmp_project / "out"
    result = pipeline.compile_to_markdown(tmp_project, output_dir=output_dir, temp_dir=tmp_project / "tmp")

    assert "ch3" not in result["markdown_files"]
    assert "ch1" in result["markdown_files"]


def test_parses_inputs(tmp_project):
    """A project structured with \\input (not \\include) yields one chapter per
    top-level \\input, in source order."""
    main = tmp_project / "book.tex"
    main.write_text(
        "\\documentclass{book}\n"
        "\\begin{document}\n"
        "\\input{ch1}\n"
        "\\input{ch2}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    (tmp_project / "ch1.tex").write_text("chapter one", encoding="utf-8")
    (tmp_project / "ch2.tex").write_text("chapter two", encoding="utf-8")

    pipeline = TexPipeline()
    output_dir = tmp_project / "out"
    result = pipeline.compile_to_markdown(tmp_project, output_dir=output_dir, temp_dir=tmp_project / "tmp")

    assert result["errors"] == []
    assert list(result["markdown_files"].keys()) == ["ch1", "ch2"]
    assert Path(result["markdown_files"]["ch1"]).exists()
    assert Path(result["markdown_files"]["ch2"]).exists()


def test_mixed_input_and_include_preserve_source_order(tmp_project):
    """\\input and \\include interleaved must be collected in source order, so
    the chapter list stays aligned with pandoc's top-level output structure."""
    main = tmp_project / "book.tex"
    main.write_text(
        "\\documentclass{book}\n"
        "\\begin{document}\n"
        "\\include{ch1}\n"
        "\\input{ch2}\n"
        "\\include{ch3}\n"
        "\\input{ch4}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    for name in ("ch1", "ch2", "ch3", "ch4"):
        (tmp_project / f"{name}.tex").write_text(f"content {name}", encoding="utf-8")

    pipeline = TexPipeline()
    config = pipeline.detect_and_resolve_config(tmp_project)
    assert config["chapters"] == ["ch1", "ch2", "ch3", "ch4"]

    result = pipeline.compile_to_markdown(
        tmp_project, output_dir=tmp_project / "out", temp_dir=tmp_project / "tmp"
    )
    assert result["errors"] == []
    assert list(result["markdown_files"].keys()) == ["ch1", "ch2", "ch3", "ch4"]


def test_preamble_input_is_not_a_chapter(tmp_project):
    """A preamble \\input (macros / package loads, before \\begin{document})
    must never be taken for a chapter."""
    main = tmp_project / "book.tex"
    main.write_text(
        "\\documentclass{book}\n"
        "\\input{macros}\n"
        "\\begin{document}\n"
        "\\input{ch1}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    (tmp_project / "macros.tex").write_text("\\newcommand{\\foo}{bar}", encoding="utf-8")
    (tmp_project / "ch1.tex").write_text("chapter one", encoding="utf-8")

    pipeline = TexPipeline()
    config = pipeline.detect_and_resolve_config(tmp_project)

    assert config["chapters"] == ["ch1"]
    assert "macros" not in config["chapters"]


def test_input_in_subdirectory_resolves(tmp_project):
    """A subdirectory \\input target resolves via the subdir and implicit .tex
    extension; the chapter key is the file stem."""
    main = tmp_project / "book.tex"
    main.write_text(
        "\\documentclass{book}\n"
        "\\begin{document}\n"
        "\\input{chapters/intro}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    (tmp_project / "chapters").mkdir()
    (tmp_project / "chapters" / "intro.tex").write_text("intro content", encoding="utf-8")

    pipeline = TexPipeline()
    output_dir = tmp_project / "out"
    result = pipeline.compile_to_markdown(tmp_project, output_dir=output_dir, temp_dir=tmp_project / "tmp")

    assert result["errors"] == []
    assert list(result["markdown_files"].keys()) == ["intro"]
    assert Path(result["markdown_files"]["intro"]).exists()


def test_skips_commented_inputs(tmp_project):
    """A commented-out \\input is toggled off and must be skipped."""
    main = tmp_project / "book.tex"
    main.write_text(
        "\\documentclass{book}\n"
        "\\begin{document}\n"
        "\\input{ch1}\n"
        "%\\input{ch2}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    (tmp_project / "ch1.tex").write_text("chapter one", encoding="utf-8")
    (tmp_project / "ch2.tex").write_text("chapter two", encoding="utf-8")

    pipeline = TexPipeline()
    output_dir = tmp_project / "out"
    result = pipeline.compile_to_markdown(tmp_project, output_dir=output_dir, temp_dir=tmp_project / "tmp")

    assert "ch2" not in result["markdown_files"]
    assert "ch1" in result["markdown_files"]


def test_single_file_no_includes(tmp_project):
    main = tmp_project / "book.tex"
    main.write_text(
        "\\documentclass{article}\n\\begin{document}\nhello\n\\end{document}\n",
        encoding="utf-8",
    )

    pipeline = TexPipeline()
    output_dir = tmp_project / "out"
    result = pipeline.compile_to_markdown(tmp_project, output_dir=output_dir, temp_dir=tmp_project / "tmp")

    assert result["errors"] == []
    assert len(result["markdown_files"]) == 1
    assert "book" in result["markdown_files"]


def test_no_main_file_found(tmp_project):
    (tmp_project / "notmain.tex") .write_text("just some text", encoding="utf-8")

    pipeline = TexPipeline()
    result = pipeline.compile_to_markdown(tmp_project, output_dir=tmp_project / "out", temp_dir=tmp_project / "tmp")

    assert result["markdown_files"] == {}
    assert result["errors"]
    assert "No .tex file with" in result["errors"][0]


class TestReadStageDetectsTexProjects:
    """A .tex project must reach the manifest gate even when the configured
    pipeline is the rmd default -- it used to find no files, report no errors,
    and leave the manifest page silently empty."""

    def test_single_file_tex_project_builds_manifest_under_rmd_default(self, tmp_project):
        project = tmp_project / "project"
        project.mkdir()
        (project / "paper.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nhi\n\\end{document}\n",
            encoding="utf-8",
        )

        result = _skeleton_run(project, tmp_project, "rmd")

        manifest = result["manifest"]
        assert len(manifest) == 1
        assert manifest[0]["name"] == "paper.tex"
        assert Path(manifest[0]["output_path"]).name == "paper.ptx"

        codes = {w["code"] for w in result["read_stage_warnings"]}
        assert "pipeline_autodetected" in codes

    def test_project_with_no_source_files_warns_instead_of_failing_silently(self, tmp_project):
        project = tmp_project / "project"
        project.mkdir()
        (project / "notes.txt").write_text("nothing translatable here", encoding="utf-8")

        result = _skeleton_run(project, tmp_project, "rmd")

        assert result["manifest"] == []
        codes = {w["code"] for w in result["read_stage_warnings"]}
        assert "no_source_files" in codes
        assert "empty_manifest" in codes

    def test_rmd_project_is_not_diverted_by_incidental_tex_files(self, tmp_project):
        """A bookdown project shipping a preamble.tex stays on the rmd pipeline."""
        project = tmp_project / "project"
        project.mkdir()
        (project / "index.Rmd").write_text("# Chapter\n", encoding="utf-8")
        (project / "preamble.tex").write_text("\\usepackage{amsmath}\n", encoding="utf-8")

        result = _skeleton_run(project, tmp_project, "rmd")

        codes = {w["code"] for w in result["read_stage_warnings"]}
        assert "pipeline_autodetected" not in codes


def test_split_pretext_by_section():
    xml_text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<pretext>"
        '<section xml:id="ch1"><title>One</title><p>Content one</p></section>'
        '<section xml:id="ch2"><title>Two</title><p>Content two</p></section>'
        "</pretext>"
    )
    mapping, warnings = split_pretext_by_section(xml_text, ["ch1", "ch2"])

    assert warnings == []
    assert set(mapping.keys()) == {"ch1", "ch2"}
    assert 'xml:id="ch1"' in mapping["ch1"]
    assert "Content one" in mapping["ch1"]
    assert 'xml:id="ch2"' in mapping["ch2"]
    assert "Content two" in mapping["ch2"]


def test_split_count_mismatch():
    xml_text = (
        "<pretext>"
        '<section xml:id="a"><p>A</p></section>'
        '<section xml:id="b"><p>B</p></section>'
        '<section xml:id="c"><p>C</p></section>'
        "</pretext>"
    )
    mapping, warnings = split_pretext_by_section(xml_text, ["ch1", "ch2"])

    assert len(warnings) == 1
    assert "mismatch" in warnings[0].lower()
    assert mapping == {"ch1": xml_text}
