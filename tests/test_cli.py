"""
Test suite for lyretext.cli module.

Tests CLI argument parsing, state handling, resume logic, and error handling
WITHOUT invoking real graph execution, LLM calls, or expensive I/O operations.
All expensive operations are mocked.
"""

import argparse
import json
import pytest
from unittest.mock import Mock, MagicMock, patch, call
from pathlib import Path

from lyretext.cli import (
    build_parser,
    cmd_start,
    cmd_resume,
    cmd_status,
    cmd_manifest,
    cmd_inspect,
    cmd_list,
    cmd_stream,
    _build_resume_value,
    _make_checkpointer,
    _run_response,
    _pretty_json,
    _get_pending_interrupts,
)


# ============================================================================
# Test fixtures
# ============================================================================

@pytest.fixture
def mock_checkpointer():
    """Mock checkpointer instance."""
    checkpointer = MagicMock()
    checkpointer.list_threads = MagicMock(return_value=["thread-1", "thread-2"])
    return checkpointer


@pytest.fixture
def mock_graph():
    """Mock compiled LangGraph instance."""
    graph = MagicMock()
    graph.get_state = MagicMock()
    graph.stream = MagicMock()
    return graph


@pytest.fixture
def sample_graph_result():
    """Sample result from graph execution (before RunResponse wrapping)."""
    return {
        "run_id": "test-run-123",
        "stage_id": "read",
        "status": "interrupted",
        "interrupted": True,
        "pending_interrupts": [
            {
                "interrupt_id": "int-0",
                "type": "validation_gate_blocked",
                "chapter_id": None,
                "value": {"type": "validation_gate_blocked", "decision": {"allowed": False}},
            }
        ],
        "manifest": [
            {"output_path": "ch1.md"},
            {"output_path": "ch2.md"},
        ],
        "chapter_status": {},
        "last_checkpoint_id": "ckpt-abc",
        "checkpoint_namespace": "default",
    }


@pytest.fixture
def sample_snapshot():
    """Mock snapshot from graph.get_state()."""
    snapshot = MagicMock()
    snapshot.values = {
        "run_id": "test-run-123",
        "manifest": [{"output_path": "ch1.md"}, {"output_path": "ch2.md"}],
        "chapter_status": {"ch1": "pending", "ch2": "pending"},
    }
    snapshot.config = {"configurable": {"thread_id": "test-run-123"}}
    snapshot.interrupts = [
        MagicMock(id="int-0", value={"type": "validation_gate_blocked", "chapter_id": None})
    ]
    return snapshot


# ============================================================================
# Test parser
# ============================================================================

class TestParser:
    """Test argument parser construction and validation."""

    def test_parser_has_all_subcommands(self):
        """Parser should have all 7 subcommands."""
        parser = build_parser()
        subparsers_actions = [
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ]
        assert len(subparsers_actions) > 0
        choices = subparsers_actions[0].choices
        expected = {"start", "resume", "status", "manifest", "stream", "list", "inspect"}
        assert set(choices.keys()) >= expected

    def test_global_flags(self):
        """Parser should accept global flags before subcommand."""
        parser = build_parser()
        args = parser.parse_args([
            "--config", "custom.yml",
            "--source", "src/",
            "--temp", "tmp/",
            "--output", "out/",
            "--checkpointer", "memory",
            "start"
        ])
        assert args.config == "custom.yml"
        assert args.source == "src/"
        assert args.temp == "tmp/"
        assert args.output == "out/"
        assert args.checkpointer == "memory"
        assert args.command == "start"

    def test_start_subcommand_parsed(self):
        """Start subcommand should parse without args."""
        parser = build_parser()
        args = parser.parse_args(["start"])
        assert args.command == "start"

    def test_resume_requires_run_id_and_action(self):
        """Resume subcommand requires run_id and --action."""
        parser = build_parser()
        
        # Missing action should fail
        with pytest.raises(SystemExit):
            parser.parse_args(["resume", "run-123"])
        
        # With action should work
        args = parser.parse_args(["resume", "run-123", "--action", "approve_continue"])
        assert args.command == "resume"
        assert args.run_id == "run-123"
        assert args.action == "approve_continue"

    def test_resume_optional_chapter_decisions(self):
        """Resume can accept optional --chapter-decisions."""
        parser = build_parser()
        args = parser.parse_args([
            "resume", "run-123", "--action", "approve",
            "--chapter-decisions", '{"ch1": "approve"}'
        ])
        assert args.chapter_decisions == '{"ch1": "approve"}'

    def test_status_requires_run_id(self):
        """Status subcommand requires run_id."""
        parser = build_parser()
        args = parser.parse_args(["status", "run-123"])
        assert args.command == "status"
        assert args.run_id == "run-123"

    def test_manifest_optional_raw_flag(self):
        """Manifest accepts optional --raw flag."""
        parser = build_parser()
        args = parser.parse_args(["manifest", "run-123", "--raw"])
        assert args.raw is True


# ============================================================================
# Test _build_resume_value (multi-interrupt logic)
# ============================================================================

class TestBuildResumeValue:
    """Test resume value construction for different interrupt scenarios."""

    def test_read_gate_single_interrupt(self):
        """Single read gate interrupt returns plain dict."""
        pending = [
            {
                "interrupt_id": "int-0",
                "type": "validation_gate_blocked",
                "chapter_id": None,
            }
        ]
        result = _build_resume_value(
            action="approve_continue",
            payload={"extra": "data"},
            chapter_decisions=None,
            pending_interrupts=pending,
        )
        assert result == {"action": "approve_continue", "extra": "data"}

    def test_single_chapter_interrupt(self):
        """Single chapter interrupt returns plain dict."""
        pending = [
            {
                "interrupt_id": "int-0",
                "type": "chapter_review",
                "chapter_id": "ch1",
            }
        ]
        result = _build_resume_value(
            action="approve",
            payload={},
            chapter_decisions=None,
            pending_interrupts=pending,
        )
        assert result == {"action": "approve"}

    def test_multiple_chapter_interrupts_with_decisions(self):
        """Multiple chapter interrupts with per-chapter decisions maps to interrupt_id dict."""
        pending = [
            {
                "interrupt_id": "int-0",
                "type": "chapter_review",
                "chapter_id": "ch1",
            },
            {
                "interrupt_id": "int-1",
                "type": "chapter_review",
                "chapter_id": "ch2",
            },
        ]
        decisions = {"ch1": "approve", "ch2": "retry"}
        result = _build_resume_value(
            action="dummy",  # Ignored when per-chapter decisions provided
            payload={},
            chapter_decisions=decisions,
            pending_interrupts=pending,
        )
        # Should map chapter_id to interrupt_id
        assert "int-0" in result
        assert "int-1" in result
        assert result["int-0"] == {"action": "approve"}
        assert result["int-1"] == {"action": "retry"}

    def test_multiple_chapter_interrupts_no_decisions_same_action(self):
        """Multiple chapter interrupts without per-chapter decisions apply same action to all."""
        pending = [
            {
                "interrupt_id": "int-0",
                "type": "chapter_review",
                "chapter_id": "ch1",
            },
            {
                "interrupt_id": "int-1",
                "type": "chapter_review",
                "chapter_id": "ch2",
            },
        ]
        result = _build_resume_value(
            action="approve",
            payload={},
            chapter_decisions=None,
            pending_interrupts=pending,
        )
        # LangGraph-required dict form for multiple interrupts
        assert result == {
            "int-0": {"action": "approve"},
            "int-1": {"action": "approve"},
        }

    def test_no_pending_interrupts(self):
        """Empty pending_interrupts returns plain dict."""
        result = _build_resume_value(
            action="approve",
            payload={"key": "value"},
            chapter_decisions=None,
            pending_interrupts=[],
        )
        assert result == {"action": "approve", "key": "value"}


# ============================================================================
# Test RunResponse construction
# ============================================================================

class TestRunResponse:
    """Test RunResponse envelope construction."""

    def test_run_response_structure(self, sample_graph_result):
        """RunResponse should have all required fields."""
        response = _run_response(sample_graph_result)
        required_fields = {
            "run_id", "stage_id", "status", "interrupted",
            "pending_interrupts", "manifest_count", "chapter_status",
            "checkpoint_id", "checkpoint_ns"
        }
        assert set(response.keys()) >= required_fields

    def test_run_response_values(self, sample_graph_result):
        """RunResponse should correctly extract values."""
        response = _run_response(sample_graph_result)
        assert response["run_id"] == "test-run-123"
        assert response["stage_id"] == "read"
        assert response["status"] == "interrupted"
        assert response["interrupted"] is True
        assert response["manifest_count"] == 2
        assert len(response["pending_interrupts"]) == 1

    def test_pretty_json(self):
        """_pretty_json should serialize dict to indented JSON."""
        data = {"key": "value", "nested": {"inner": 123}}
        result = _pretty_json(data)
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed["key"] == "value"
        assert parsed["nested"]["inner"] == 123


# ============================================================================
# Test cmd_start
# ============================================================================

class TestCmdStart:
    """Test start subcommand handler."""

    @patch("lyretext.cli.invoke_workflow_graph")
    @patch("lyretext.cli._make_checkpointer")
    @patch("sys.stdout")
    def test_cmd_start_success(self, mock_stdout, mock_checkpointer_fn, mock_invoke, sample_graph_result):
        """cmd_start should invoke graph and print RunResponse."""
        mock_checkpointer_fn.return_value = MagicMock()
        mock_invoke.return_value = sample_graph_result

        args = argparse.Namespace(
            source="examples/src",
            temp="examples/tmp",
            output="examples/out",
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        result = cmd_start(args)

        # Should return 0 (success)
        assert result == 0

        # Should call invoke_workflow_graph with correct state
        mock_invoke.assert_called_once()
        call_args = mock_invoke.call_args
        initial_state = call_args[0][0]
        assert initial_state["project_source"] == "examples/src"
        assert initial_state["temp_dir"] == "examples/tmp"
        assert initial_state["output_dir"] == "examples/out"

    @patch("lyretext.cli.invoke_workflow_graph")
    @patch("lyretext.cli._make_checkpointer")
    def test_cmd_start_uses_checkpointer(self, mock_checkpointer_fn, mock_invoke, sample_graph_result):
        """cmd_start should pass checkpointer to invoke_workflow_graph."""
        mock_checkpointer = MagicMock()
        mock_checkpointer_fn.return_value = mock_checkpointer
        mock_invoke.return_value = sample_graph_result

        args = argparse.Namespace(
            source="src",
            temp="tmp",
            output="out",
            config="config.yml",
            checkpointer="sqlite",
            db_path="runs.db",
        )

        cmd_start(args)

        # Should use sqlite backend
        mock_checkpointer_fn.assert_called_once_with(args)

        # Should pass checkpointer to invoke
        mock_invoke.assert_called_once()
        assert mock_invoke.call_args[1]["checkpointer"] == mock_checkpointer


# ============================================================================
# Test cmd_resume
# ============================================================================

class TestCmdResume:
    """Test resume subcommand handler."""

    @patch("lyretext.cli.resume_workflow_graph")
    @patch("lyretext.cli._get_pending_interrupts")
    @patch("lyretext.cli._make_checkpointer")
    def test_cmd_resume_read_gate_approval(
        self, mock_checkpointer_fn, mock_get_interrupts, mock_resume
    ):
        """Resume with read gate interrupt."""
        mock_checkpointer = MagicMock()
        mock_checkpointer_fn.return_value = mock_checkpointer
        mock_get_interrupts.return_value = [
            {"interrupt_id": "int-0", "type": "validation_gate_blocked", "chapter_id": None}
        ]
        mock_resume.return_value = {"run_id": "test-run", "status": "running"}

        args = argparse.Namespace(
            run_id="test-run",
            action="approve_continue",
            payload=None,
            chapter_decisions=None,
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        result = cmd_resume(args)

        assert result == 0
        mock_resume.assert_called_once()
        resume_call = mock_resume.call_args
        resume_value = resume_call[1]["resume_value"]
        assert resume_value == {"action": "approve_continue"}

    @patch("lyretext.cli.resume_workflow_graph")
    @patch("lyretext.cli._get_pending_interrupts")
    @patch("lyretext.cli._make_checkpointer")
    def test_cmd_resume_chapter_decisions(
        self, mock_checkpointer_fn, mock_get_interrupts, mock_resume
    ):
        """Resume with per-chapter decisions."""
        mock_checkpointer = MagicMock()
        mock_checkpointer_fn.return_value = mock_checkpointer
        mock_get_interrupts.return_value = [
            {"interrupt_id": "int-0", "type": "chapter_review", "chapter_id": "ch1"},
            {"interrupt_id": "int-1", "type": "chapter_review", "chapter_id": "ch2"},
        ]
        mock_resume.return_value = {"run_id": "test-run", "status": "interrupted"}

        args = argparse.Namespace(
            run_id="test-run",
            action="dummy",
            payload=None,
            chapter_decisions='{"ch1": "approve", "ch2": "retry"}',
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        result = cmd_resume(args)

        assert result == 0
        resume_call = mock_resume.call_args
        resume_value = resume_call[1]["resume_value"]
        # Should map chapter decisions to interrupt IDs
        assert "int-0" in resume_value
        assert "int-1" in resume_value
        assert resume_value["int-0"]["action"] == "approve"
        assert resume_value["int-1"]["action"] == "retry"

    @patch("sys.stderr")
    def test_cmd_resume_invalid_json(self, mock_stderr):
        """Resume with invalid JSON should print error."""
        args = argparse.Namespace(
            run_id="test-run",
            action="approve",
            payload=None,
            chapter_decisions='{"invalid: json}',  # Invalid
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        result = cmd_resume(args)

        # Should return 1 (error)
        assert result == 1

    @patch("lyretext.cli.resume_workflow_graph")
    @patch("lyretext.cli._get_pending_interrupts")
    @patch("lyretext.cli._make_checkpointer")
    def test_cmd_resume_with_payload(
        self, mock_checkpointer_fn, mock_get_interrupts, mock_resume
    ):
        """Resume can include extra payload data."""
        mock_checkpointer = MagicMock()
        mock_checkpointer_fn.return_value = mock_checkpointer
        mock_get_interrupts.return_value = [
            {"interrupt_id": "int-0", "type": "validation_gate_blocked", "chapter_id": None}
        ]
        mock_resume.return_value = {}

        args = argparse.Namespace(
            run_id="test-run",
            action="edit_manifest",
            payload='{"chapter_ids": ["ch1", "ch2"]}',
            chapter_decisions=None,
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        result = cmd_resume(args)

        assert result == 0
        resume_value = mock_resume.call_args[1]["resume_value"]
        assert resume_value["action"] == "edit_manifest"
        assert resume_value["chapter_ids"] == ["ch1", "ch2"]


# ============================================================================
# Test cmd_status
# ============================================================================

class TestCmdStatus:
    """Test status subcommand handler."""

    @patch("lyretext.cli.build_workflow_graph")
    @patch("lyretext.cli._make_checkpointer")
    def test_cmd_status_success(self, mock_checkpointer_fn, mock_build_graph, sample_snapshot):
        """Status should retrieve and print current state."""
        mock_checkpointer = MagicMock()
        mock_checkpointer_fn.return_value = mock_checkpointer
        mock_graph = MagicMock()
        mock_graph.get_state.return_value = sample_snapshot
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(
            run_id="test-run",
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        result = cmd_status(args)

        assert result == 0
        mock_graph.get_state.assert_called_once()

    @patch("lyretext.cli.build_workflow_graph")
    @patch("lyretext.cli._make_checkpointer")
    def test_cmd_status_not_found(self, mock_checkpointer_fn, mock_build_graph):
        """Status with non-existent run should return error."""
        mock_checkpointer = MagicMock()
        mock_checkpointer_fn.return_value = mock_checkpointer
        mock_graph = MagicMock()
        mock_graph.get_state.return_value = None
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(
            run_id="nonexistent",
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        result = cmd_status(args)

        assert result == 1


# ============================================================================
# Test cmd_manifest
# ============================================================================

class TestCmdManifest:
    """Test manifest subcommand handler."""

    @patch("lyretext.cli.build_workflow_graph")
    @patch("lyretext.cli._make_checkpointer")
    def test_cmd_manifest_compact(self, mock_checkpointer_fn, mock_build_graph, sample_snapshot):
        """Manifest without --raw should list chapter names."""
        mock_checkpointer = MagicMock()
        mock_checkpointer_fn.return_value = mock_checkpointer
        mock_graph = MagicMock()
        mock_graph.get_state.return_value = sample_snapshot
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(
            run_id="test-run",
            raw=False,
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        result = cmd_manifest(args)

        assert result == 0

    @patch("lyretext.cli.build_workflow_graph")
    @patch("lyretext.cli._make_checkpointer")
    def test_cmd_manifest_raw(self, mock_checkpointer_fn, mock_build_graph, sample_snapshot):
        """Manifest with --raw should output JSON."""
        mock_checkpointer = MagicMock()
        mock_checkpointer_fn.return_value = mock_checkpointer
        mock_graph = MagicMock()
        mock_graph.get_state.return_value = sample_snapshot
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(
            run_id="test-run",
            raw=True,
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        result = cmd_manifest(args)

        assert result == 0


# ============================================================================
# Test cmd_inspect
# ============================================================================

class TestCmdInspect:
    """Test inspect subcommand handler."""

    @patch("lyretext.cli.build_workflow_graph")
    @patch("lyretext.cli._make_checkpointer")
    def test_cmd_inspect_success(self, mock_checkpointer_fn, mock_build_graph, sample_snapshot):
        """Inspect should dump full state as JSON."""
        mock_checkpointer = MagicMock()
        mock_checkpointer_fn.return_value = mock_checkpointer
        mock_graph = MagicMock()
        mock_graph.get_state.return_value = sample_snapshot
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(
            run_id="test-run",
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        result = cmd_inspect(args)

        assert result == 0

    @patch("lyretext.cli.build_workflow_graph")
    @patch("lyretext.cli._make_checkpointer")
    def test_cmd_inspect_not_found(self, mock_checkpointer_fn, mock_build_graph):
        """Inspect with non-existent run should return error."""
        mock_checkpointer = MagicMock()
        mock_checkpointer_fn.return_value = mock_checkpointer
        mock_graph = MagicMock()
        mock_graph.get_state.return_value = None
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(
            run_id="nonexistent",
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        result = cmd_inspect(args)

        assert result == 1


# ============================================================================
# Test cmd_list
# ============================================================================

class TestCmdList:
    """Test list subcommand handler."""

    @patch("lyretext.cli._make_checkpointer")
    def test_cmd_list_success(self, mock_checkpointer_fn):
        """List should enumerate threads from checkpointer."""
        mock_checkpointer = MagicMock()
        mock_checkpointer.list_threads = MagicMock(return_value=["thread-1", "thread-2"])
        mock_checkpointer_fn.return_value = mock_checkpointer

        args = argparse.Namespace(
            config="config.yml",
            checkpointer="sqlite",
            db_path="runs.db",
        )

        result = cmd_list(args)

        assert result == 0

    @patch("lyretext.cli._make_checkpointer")
    def test_cmd_list_memory_backend_error(self, mock_checkpointer_fn):
        """List with memory backend should return error."""
        mock_checkpointer = MagicMock(spec=[])  # No list_threads method
        mock_checkpointer_fn.return_value = mock_checkpointer

        args = argparse.Namespace(
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        result = cmd_list(args)

        # Should return error (1)
        assert result == 1


# ============================================================================
# Test cmd_stream
# ============================================================================

class TestCmdStream:
    """Test stream subcommand handler."""

    @patch("lyretext.cli.build_workflow_graph")
    @patch("lyretext.cli._make_checkpointer")
    def test_cmd_stream_with_interrupts(self, mock_checkpointer_fn, mock_build_graph):
        """Stream should emit events for pending interrupts."""
        mock_checkpointer = MagicMock()
        mock_checkpointer_fn.return_value = mock_checkpointer
        
        # Mock snapshot with interrupts
        snapshot = MagicMock()
        intr = MagicMock()
        intr.id = "int-0"
        intr.value = {"type": "chapter_review", "chapter_id": "ch1"}
        snapshot.interrupts = [intr]
        snapshot.next = ()
        
        mock_graph = MagicMock()
        mock_graph.get_state.return_value = snapshot
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(
            run_id="test-run",
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        result = cmd_stream(args)

        assert result == 0


# ============================================================================
# Test _make_checkpointer
# ============================================================================

class TestMakeCheckpointer:
    """Test checkpointer factory."""

    def test_make_checkpointer_memory(self):
        """Should create memory checkpointer."""
        args = argparse.Namespace(checkpointer="memory", db_path=None)
        checkpointer = _make_checkpointer(args)
        # MemorySaver is the type
        from langgraph.checkpoint.memory import MemorySaver
        assert isinstance(checkpointer, MemorySaver)

    def test_make_checkpointer_sqlite(self):
        """Should create sqlite checkpointer."""
        args = argparse.Namespace(checkpointer="sqlite", db_path=":memory:")
        checkpointer = _make_checkpointer(args)
        # SqliteSaver is the type
        from langgraph.checkpoint.sqlite import SqliteSaver
        assert isinstance(checkpointer, SqliteSaver)

    def test_make_checkpointer_default_backend(self):
        """Default backend should be memory."""
        args = argparse.Namespace(checkpointer=None, db_path=None)
        # Override default in the call
        args.checkpointer = "memory"
        checkpointer = _make_checkpointer(args)
        from langgraph.checkpoint.memory import MemorySaver
        assert isinstance(checkpointer, MemorySaver)


# ============================================================================
# Test error handling
# ============================================================================

class TestErrorHandling:
    """Test error handling in CLI."""

    @patch("sys.stderr")
    def test_resume_json_decode_error_message(self, mock_stderr):
        """Invalid JSON should show helpful error message."""
        args = argparse.Namespace(
            run_id="test-run",
            action="approve",
            payload='{"bad json}',  # Missing closing brace
            chapter_decisions=None,
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        result = cmd_resume(args)

        assert result == 1

    @patch("lyretext.cli.build_workflow_graph")
    @patch("lyretext.cli._make_checkpointer")
    def test_status_handles_missing_manifest(self, mock_checkpointer_fn, mock_build_graph):
        """Status should handle missing manifest gracefully."""
        mock_checkpointer = MagicMock()
        mock_checkpointer_fn.return_value = mock_checkpointer
        
        # Snapshot without manifest
        snapshot = MagicMock()
        snapshot.values = {"run_id": "test-run"}
        snapshot.config = {"configurable": {"thread_id": "test-run"}}
        snapshot.interrupts = ()
        
        mock_graph = MagicMock()
        mock_graph.get_state.return_value = snapshot
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(
            run_id="test-run",
            config="config.yml",
            checkpointer="memory",
            db_path=None,
        )

        # Should not crash
        result = cmd_status(args)
        assert result == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
