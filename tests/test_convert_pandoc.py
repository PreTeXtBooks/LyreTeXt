"""Tests for the deterministic Markdown → PreTeXt conversion step.

Covers lyretext.convert.pandoc, the rewritten translate_chapter node, the
deterministic xml_wellformed check, and the bounded review→edit loop routing.

No LLM calls anywhere in this file.
"""
from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lyretext.format import format_pretext
from lyretext.convert.pandoc import (
    PandocNotFoundError,
    convert_markdown_to_pretext,
    find_pandoc,
)
from lyretext.review.checks import check_wellformed
from lyretext.translate.agents import translate_chapter
from lyretext.translate.graph import route_after_review

HAS_PANDOC = shutil.which("pandoc") is not None

SAMPLE_MD = """# Chapter One

Some text with $x^2$ math.

## Section A

- item one
- item two

::: {.theorem}
A theorem.
:::
"""


def _run_config(**overrides):
    """Build a RunnableConfig carrying resolved runtime options."""
    global_options = {
        "execution_mode": "direct",
        "apply_mode": "auto_apply",
        "create_backup": False,
        "provider": "gemini",
        "verbosity": "normal",
        "pipeline": "rmd",
        "pandoc_exe": None,
        "pandoc_writer": "pretext.lua",
        "auto_edit": True,
        "max_edit_iterations": 2,
    }
    global_options.update(overrides)
    return {"configurable": {"runtime_options": {"global_options": global_options}}}


# ---------------------------------------------------------------------------
# find_pandoc
# ---------------------------------------------------------------------------

class TestFindPandoc:
    def test_explicit_wins(self):
        assert find_pandoc(r"C:\custom\pandoc.exe") == r"C:\custom\pandoc.exe"

    def test_env_var_used_when_no_explicit(self, monkeypatch):
        monkeypatch.setenv("PANDOC_EXE", "/opt/pandoc")
        assert find_pandoc() == "/opt/pandoc"

    def test_falls_back_to_path(self, monkeypatch):
        monkeypatch.delenv("PANDOC_EXE", raising=False)
        with patch("lyretext.convert.pandoc.shutil.which", return_value="/usr/bin/pandoc"):
            assert find_pandoc() == "/usr/bin/pandoc"

    def test_raises_when_missing(self, monkeypatch):
        monkeypatch.delenv("PANDOC_EXE", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", "")
        with (
            patch("lyretext.convert.pandoc.shutil.which", return_value=None),
            patch("lyretext.convert.pandoc.glob.glob", return_value=[]),
        ):
            with pytest.raises(PandocNotFoundError, match="pandoc was not found"):
                find_pandoc()


# ---------------------------------------------------------------------------
# convert_markdown_to_pretext — mocked subprocess
# ---------------------------------------------------------------------------

class TestConvertMocked:
    def _md(self, tmp_path: Path) -> Path:
        src = tmp_path / "ch1.md"
        src.write_text(SAMPLE_MD, encoding="utf-8")
        return src

    def test_builds_expected_command(self, tmp_path):
        src = self._md(tmp_path)
        completed = MagicMock(returncode=0, stdout="<section/>", stderr="")
        with (
            patch("lyretext.convert.pandoc.find_pandoc", return_value="pandoc"),
            patch("lyretext.convert.pandoc.subprocess.run", return_value=completed) as run,
        ):
            xml, messages = convert_markdown_to_pretext(src)

        assert xml == "<section/>"
        assert messages == []
        assert run.call_args.args[0] == ["pandoc", str(src), "-t", "pretext.lua"]

    def test_forces_utf8_decoding(self, tmp_path):
        """Without this, Windows decodes pandoc's UTF-8 output as cp1252."""
        src = self._md(tmp_path)
        completed = MagicMock(returncode=0, stdout="<section/>", stderr="")
        with (
            patch("lyretext.convert.pandoc.find_pandoc", return_value="pandoc"),
            patch("lyretext.convert.pandoc.subprocess.run", return_value=completed) as run,
        ):
            convert_markdown_to_pretext(src)

        assert run.call_args.kwargs["encoding"] == "utf-8"

    def test_custom_writer_and_extra_args(self, tmp_path):
        src = self._md(tmp_path)
        completed = MagicMock(returncode=0, stdout="<section/>", stderr="")
        with (
            patch("lyretext.convert.pandoc.find_pandoc", return_value="pandoc"),
            patch("lyretext.convert.pandoc.subprocess.run", return_value=completed) as run,
        ):
            convert_markdown_to_pretext(
                src, writer="other.lua", extra_args=("--verbose",)
            )

        assert run.call_args.args[0] == [
            "pandoc", str(src), "-t", "other.lua", "--verbose",
        ]

    def test_nonzero_exit_returns_error(self, tmp_path):
        src = self._md(tmp_path)
        completed = MagicMock(returncode=1, stdout="", stderr="could not find pretext.lua")
        with (
            patch("lyretext.convert.pandoc.find_pandoc", return_value="pandoc"),
            patch("lyretext.convert.pandoc.subprocess.run", return_value=completed),
        ):
            xml, messages = convert_markdown_to_pretext(src)

        assert xml == ""
        assert len(messages) == 1
        assert "could not find pretext.lua" in messages[0]

    def test_timeout_returns_error(self, tmp_path):
        src = self._md(tmp_path)
        with (
            patch("lyretext.convert.pandoc.find_pandoc", return_value="pandoc"),
            patch(
                "lyretext.convert.pandoc.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="pandoc", timeout=120),
            ),
        ):
            xml, messages = convert_markdown_to_pretext(src)

        assert xml == ""
        assert "timed out" in messages[0]

    def test_success_with_stderr_is_a_warning_not_a_failure(self, tmp_path):
        """Failure is signalled by empty output, not by non-empty messages."""
        src = self._md(tmp_path)
        completed = MagicMock(returncode=0, stdout="<section/>", stderr="[WARNING] odd construct")
        with (
            patch("lyretext.convert.pandoc.find_pandoc", return_value="pandoc"),
            patch("lyretext.convert.pandoc.subprocess.run", return_value=completed),
        ):
            xml, messages = convert_markdown_to_pretext(src)

        assert xml == "<section/>"
        assert "odd construct" in messages[0]

    def test_missing_source_short_circuits(self, tmp_path):
        xml, messages = convert_markdown_to_pretext(tmp_path / "nope.md")
        assert xml == ""
        assert "not found" in messages[0]


# ---------------------------------------------------------------------------
# convert_markdown_to_pretext — the real binary
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_PANDOC, reason="pandoc not installed")
class TestConvertReal:
    def test_converts_sample_markdown(self, tmp_path):
        src = tmp_path / "ch1.md"
        src.write_text(SAMPLE_MD, encoding="utf-8")

        xml, messages = convert_markdown_to_pretext(src)

        if not xml:
            pytest.skip(f"pretext.lua writer unavailable: {messages}")

        assert "<section" in xml
        assert "<title>Chapter One</title>" in xml
        assert "<m>x^2</m>" in xml
        assert "<theorem>" in xml
        # Output must be parseable — this is what makes the deterministic
        # xml_wellformed check a near-certain no-op.
        ET.fromstring(xml)


# ---------------------------------------------------------------------------
# translate_chapter node
# ---------------------------------------------------------------------------

class TestTranslateChapterNode:
    def _state(self, tmp_path):
        src = tmp_path / "ch1.md"
        src.write_text(SAMPLE_MD, encoding="utf-8")
        return {
            "source_path": str(src),
            "output_path": str(tmp_path / "out" / "ch1.ptx"),
            "chapter_id": "ch1",
        }

    def test_writes_output_and_resets_edit_budget(self, tmp_path):
        state = self._state(tmp_path)
        with patch(
            "lyretext.translate.agents.convert_markdown_to_pretext",
            return_value=("<section><title>X</title></section>", []),
        ):
            result = translate_chapter(state, _run_config())

        # Written through the canonical formatter, like every other .ptx write.
        assert result["pretext_output"] == format_pretext("<section><title>X</title></section>")[0]
        assert result["edit_iterations"] == 0
        assert Path(state["output_path"]).read_text(encoding="utf-8") == result["pretext_output"]

    def test_passes_config_through_to_pandoc(self, tmp_path):
        state = self._state(tmp_path)
        with patch(
            "lyretext.translate.agents.convert_markdown_to_pretext",
            return_value=("<section/>", []),
        ) as convert:
            translate_chapter(
                state, _run_config(pandoc_exe="/custom/pandoc", pandoc_writer="alt.lua")
            )

        assert convert.call_args.kwargs["pandoc_exe"] == "/custom/pandoc"
        assert convert.call_args.kwargs["writer"] == "alt.lua"

    def test_dry_run_writes_nothing(self, tmp_path):
        state = self._state(tmp_path)
        with patch(
            "lyretext.translate.agents.convert_markdown_to_pretext",
            return_value=("<section/>", []),
        ):
            result = translate_chapter(state, _run_config(apply_mode="dry_run"))

        assert result["pretext_output"] == format_pretext("<section/>")[0]
        assert not Path(state["output_path"]).exists()

    def test_create_backup_preserves_previous_output(self, tmp_path):
        state = self._state(tmp_path)
        out = Path(state["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("<section>old</section>", encoding="utf-8")

        with patch(
            "lyretext.translate.agents.convert_markdown_to_pretext",
            return_value=("<section>new</section>", []),
        ):
            translate_chapter(state, _run_config(create_backup=True))

        assert out.read_text(encoding="utf-8") == format_pretext("<section>new</section>")[0]
        assert out.with_suffix(".ptx.bak").read_text(encoding="utf-8") == "<section>old</section>"

    def test_conversion_failure_becomes_blocking_finding(self, tmp_path):
        state = self._state(tmp_path)
        with patch(
            "lyretext.translate.agents.convert_markdown_to_pretext",
            return_value=("", ["pandoc conversion failed for ch1.md: boom"]),
        ):
            result = translate_chapter(state, _run_config())

        findings = result["chapter_findings"]
        assert findings["review_status"] == "blocking"
        assert findings["counts"]["error"] == 1
        issue = findings["issues"][0]
        assert issue["check_id"] == "pandoc_convert"
        assert issue["auto_fixable"] is False
        assert not Path(state["output_path"]).exists()

    def test_missing_pandoc_becomes_blocking_finding(self, tmp_path):
        state = self._state(tmp_path)
        with patch(
            "lyretext.translate.agents.convert_markdown_to_pretext",
            side_effect=PandocNotFoundError("pandoc was not found."),
        ):
            result = translate_chapter(state, _run_config())

        assert result["chapter_findings"]["review_status"] == "blocking"
        assert result["pretext_output"] == ""


# ---------------------------------------------------------------------------
# Deterministic xml_wellformed check
# ---------------------------------------------------------------------------

class TestCheckWellformed:
    def test_valid_xml_yields_no_issues(self):
        assert check_wellformed({"artifact": "<section><title>A</title></section>"}) == {
            "issues": []
        }

    def test_empty_artifact_yields_no_issues(self):
        assert check_wellformed({"artifact": "   "}) == {"issues": []}

    def test_malformed_xml_reports_error_with_line(self):
        result = check_wellformed({"artifact": "<section>\n<title>A</section>"})
        issues = result["issues"]
        assert len(issues) == 1
        assert issues[0].check_id == "xml_wellformed"
        assert issues[0].severity == "error"
        assert issues[0].line is not None
        assert issues[0].auto_fixable is True

    def test_unescaped_ampersand_is_caught(self):
        result = check_wellformed({"artifact": "<p>Tom & Jerry</p>"})
        assert len(result["issues"]) == 1


# ---------------------------------------------------------------------------
# Bounded review → edit loop
# ---------------------------------------------------------------------------

class TestRouteAfterReview:
    GATE = "chapter_review_gate"

    def _findings(self, *, auto_fixable=True, ignored=False):
        return {
            "issues": [
                {
                    "check_id": "math_notation",
                    "severity": "warn",
                    "message": "bare $",
                    "auto_fixable": auto_fixable,
                    "ignored": ignored,
                }
            ]
        }

    def test_routes_to_editor_when_actionable(self):
        state = {"chapter_findings": self._findings(), "edit_iterations": 0}
        assert route_after_review(state, _run_config()) == "edit_chapter"

    def test_auto_edit_is_off_by_default(self):
        """The loop is opt-in: unasked repair defeats the point of the gate."""
        state = {"chapter_findings": self._findings(), "edit_iterations": 0}
        assert route_after_review(state, _run_config(auto_edit=False)) == self.GATE
        # …and that is what an unconfigured caller gets.
        assert route_after_review(state, None) == self.GATE

    def test_loop_is_bounded(self):
        findings = self._findings()
        assert route_after_review(
            {"chapter_findings": findings, "edit_iterations": 1}, _run_config()
        ) == "edit_chapter"
        # At the cap the loop must terminate even though issues remain.
        assert route_after_review(
            {"chapter_findings": findings, "edit_iterations": 2}, _run_config()
        ) == self.GATE

    def test_respects_custom_iteration_cap(self):
        state = {"chapter_findings": self._findings(), "edit_iterations": 2}
        assert route_after_review(state, _run_config(max_edit_iterations=4)) == "edit_chapter"

    def test_non_fixable_issues_do_not_trigger_editor(self):
        state = {"chapter_findings": self._findings(auto_fixable=False), "edit_iterations": 0}
        assert route_after_review(state, _run_config()) == self.GATE

    def test_dismissed_issues_do_not_trigger_editor(self):
        state = {"chapter_findings": self._findings(ignored=True), "edit_iterations": 0}
        assert route_after_review(state, _run_config()) == self.GATE

    def test_no_findings_goes_to_gate(self):
        assert route_after_review({}, _run_config()) == self.GATE


# ---------------------------------------------------------------------------
# Runtime config actually reaching nodes
# ---------------------------------------------------------------------------

class TestConfigInjection:
    """LangGraph injects runtime config only into a parameter named exactly
    ``config``, and only when its annotation is one it recognises. Under
    ``from __future__ import annotations`` the annotation becomes a string, so
    the ``RunnableConfig | None`` spelling fails that check — LangGraph warns
    and silently passes None, which makes config.yml and every node_override
    inert. These tests fail loudly if that regresses."""

    def _warnings_from(self, build):
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            build()
        return [str(w.message) for w in caught if "'config' parameter" in str(w.message)]

    def test_chapter_graph_nodes_accept_config(self):
        from lyretext.translate.graph import build_chapter_graph

        assert self._warnings_from(lambda: build_chapter_graph(None)) == []

    def test_review_graph_nodes_accept_config(self):
        from lyretext.review.graph import build_review_graph

        assert self._warnings_from(lambda: build_review_graph("translate")) == []

    def test_skeleton_graph_nodes_accept_config(self):
        from lyretext.read.graph import build_skeleton_graph

        assert self._warnings_from(build_skeleton_graph) == []

    def test_config_reaches_translate_chapter_through_the_graph(self, tmp_path):
        """End-to-end proof: apply_mode=dry_run only takes effect if the
        runtime options actually arrive at the node."""
        from langgraph.types import Command

        from lyretext.orchestration.checkpointing import (
            build_chapter_checkpoint_config,
            build_checkpointer,
        )
        from lyretext.translate.graph import build_chapter_graph

        src = tmp_path / "ch1.md"
        src.write_text(SAMPLE_MD, encoding="utf-8")
        out = tmp_path / "out" / "ch1.ptx"

        graph = build_chapter_graph(build_checkpointer("memory"))
        config = build_chapter_checkpoint_config("run-cfg", "ch1")
        config["configurable"]["runtime_options"] = {
            "global_options": {"apply_mode": "dry_run", "auto_edit": False}
        }

        with patch(
            "lyretext.translate.agents.convert_markdown_to_pretext",
            return_value=("<section/>", []),
        ):
            graph.invoke(
                {"source_path": str(src), "output_path": str(out), "chapter_id": "ch1"},
                config=config,
            )
            graph.invoke(Command(resume={"action": "start"}), config=config)

        assert not out.exists(), "dry_run was ignored — runtime config never reached the node"
