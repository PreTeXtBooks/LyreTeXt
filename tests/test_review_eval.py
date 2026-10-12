"""Tests for the review-agent evaluation harness (#32).

The harness exists to turn "the review agents seem better" into a number, so the
number has to be trustworthy: these pin down the matching rule, the metric
definitions, and the recorded/live modes against synthetic data, and guard the
real golden set against drifting out of step with its own expectations.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lyretext.review.evaluation import (
    DEFAULT_GOLDEN_DIR,
    evaluate,
    load_fixture,
    load_golden_set,
    match_findings,
    prompt_version,
    score,
    validate_fixture,
)


def _f(check_id="math_notation", line=None, end_line=None, **extra):
    return {"check_id": check_id, "line": line, "end_line": end_line, **extra}


class TestMatching:
    def test_a_finding_within_tolerance_matches(self):
        matches, fp, fn = match_findings([_f(line=7)], [_f(line=5)], tolerance=2)
        assert matches == [(0, 0)] and fp == [] and fn == []

    def test_a_finding_outside_tolerance_is_both_a_false_positive_and_a_miss(self):
        matches, fp, fn = match_findings([_f(line=8)], [_f(line=5)], tolerance=2)
        assert matches == [] and fp == [0] and fn == [0]

    def test_overlapping_spans_match_regardless_of_start_line(self):
        # The model points at the closing tag of a block the golden set spans.
        matches, _, _ = match_findings([_f(line=8)], [_f(line=4, end_line=8)], tolerance=0)
        assert matches == [(0, 0)]

    def test_the_check_must_agree(self):
        matches, fp, fn = match_findings(
            [_f("pretext_structure", line=5)], [_f("math_notation", line=5)]
        )
        assert matches == [] and fp == [0] and fn == [0]

    def test_matching_is_one_to_one(self):
        """A problem reported twice is one hit and one false positive."""
        matches, fp, fn = match_findings([_f(line=5), _f(line=6)], [_f(line=5)])
        assert matches == [(0, 0)] and fp == [1] and fn == []

    def test_the_nearest_candidate_is_preferred(self):
        matches, fp, _ = match_findings([_f(line=7), _f(line=5)], [_f(line=5)])
        assert matches == [(1, 0)] and fp == [0]

    def test_the_count_does_not_depend_on_report_order(self):
        """Greedy nearest-first would pair p0-e0 and strand e1; the maximum
        matching finds both, whichever order the model reported them in."""
        produced = [_f(line=5), _f(line=3)]
        expected = [_f(line=4), _f(line=6)]
        for order in (produced, list(reversed(produced))):
            matches, fp, fn = match_findings(order, expected, tolerance=1)
            assert len(matches) == 2 and fp == [] and fn == []

    def test_an_unlocated_expectation_matches_anywhere(self):
        matches, _, _ = match_findings([_f(line=400)], [_f(line=None)])
        assert matches == [(0, 0)]

    def test_an_unlocated_finding_is_not_credited_for_a_located_problem(self):
        matches, fp, fn = match_findings([_f(line=None)], [_f(line=5)])
        assert matches == [] and fp == [0] and fn == [0]


class TestScore:
    def test_definitions(self):
        assert score(3, 1, 2) == {
            "tp": 3, "fp": 1, "fn": 2,
            "precision": 0.75, "recall": 0.6, "f1": round(2 * 0.75 * 0.6 / 1.35, 4),
        }

    def test_nothing_reported_has_undefined_precision(self):
        """Not 1.0 — silence would otherwise look like perfect precision."""
        m = score(0, 0, 3)
        assert m["precision"] is None and m["recall"] == 0.0 and m["f1"] is None

    def test_nothing_expected_has_undefined_recall(self):
        m = score(0, 2, 0)
        assert m["precision"] == 0.0 and m["recall"] is None

    def test_all_wrong_is_zero_f1(self):
        assert score(0, 1, 1)["f1"] == 0.0


class TestPromptVersion:
    def test_is_stable_and_tracks_content(self, tmp_path):
        a = tmp_path / "a.md"
        a.write_bytes(b"## check\nbe strict\n")
        b = tmp_path / "b.md"
        b.write_bytes(b"## check\nbe strict\r\n")
        c = tmp_path / "c.md"
        c.write_bytes(b"## check\nbe lenient\n")
        assert prompt_version(a) == prompt_version(a)
        assert prompt_version(a) == prompt_version(b)  # line endings are not a version
        assert prompt_version(a) != prompt_version(c)


# ---------------------------------------------------------------------------
# End-to-end over a synthetic golden set
# ---------------------------------------------------------------------------

_PTX = "\n".join([
    "<section>",                    # 1
    "  <p>The graph of $x^2$.</p>",  # 2
    "  <p>Fine <m>y</m>.</p>",       # 3
    "  <p>Also $z$.</p>",            # 4
    "</section>",                    # 5
    "",
])


def _write_fixture(tmp_path: Path, *, recording=None, checks=None, expected=None) -> Path:
    golden = tmp_path / "golden"
    golden.mkdir(exist_ok=True)
    (golden / "probe.ptx").write_text(_PTX, encoding="utf-8")
    spec = {
        "description": "synthetic",
        "checks": checks if checks is not None else ["xml_wellformed", "math_notation"],
        "expected": expected if expected is not None else [
            {"label": "dollar-2", "check_id": "math_notation", "line": 2, "anchor": "$x^2$"},
            {"label": "dollar-4", "check_id": "math_notation", "line": 4, "anchor": "$z$"},
        ],
    }
    (golden / "probe.expected.json").write_text(json.dumps(spec), encoding="utf-8")
    if recording is not None:
        (golden / "probe.recorded.json").write_text(json.dumps(recording), encoding="utf-8")
    return golden


def _recording(issues, version=None):
    return {
        "prompt_version": version or prompt_version(),
        "provider": "gemini",
        "model": "test-model",
        "recorded_at": "2026-01-01T00:00:00+00:00",
        "responses": {"math_notation": issues},
    }


class TestRecordedMode:
    def test_replays_the_recording_through_the_real_check(self, tmp_path):
        golden = _write_fixture(tmp_path, recording=_recording([
            {"severity": "warn", "line": 2, "message": "bare $ math", "suggestion": "<m>x^2</m>"},
            {"severity": "warn", "line": 3, "message": "invented problem", "suggestion": None},
        ]))
        # Exact lines: the synthetic document is too short for the default
        # window to tell line 3 apart from the expected problem on line 4.
        report = evaluate(golden, tolerance=0)

        assert report["metrics"]["by_check"]["math_notation"] == score(1, 1, 1)
        # The deterministic check ran for real and correctly found nothing.
        assert report["metrics"]["by_check"]["xml_wellformed"] == score(0, 0, 0)
        result = report["fixtures"][0]
        assert [m["label"] for m in result["matched"]] == ["dollar-2"]
        assert [fp["message"] for fp in result["false_positives"]] == ["invented problem"]
        assert [fn["label"] for fn in result["false_negatives"]] == ["dollar-4"]
        assert report["warnings"] == []

    def test_is_deterministic(self, tmp_path):
        golden = _write_fixture(tmp_path, recording=_recording([
            {"severity": "warn", "line": 2, "message": "bare $ math"},
        ]))
        first, second = evaluate(golden), evaluate(golden)
        assert first["metrics"] == second["metrics"]
        assert first["fixtures"] == second["fixtures"]

    def test_an_unrecorded_check_is_excluded_not_scored_as_misses(self, tmp_path):
        golden = _write_fixture(tmp_path)  # no recording at all
        report = evaluate(golden)
        assert "math_notation" not in report["metrics"]["by_check"]
        assert report["fixtures"][0]["unrecorded_checks"] == ["math_notation"]
        assert any("no recording for math_notation" in w for w in report["warnings"])

    def test_a_stale_recording_is_flagged(self, tmp_path):
        golden = _write_fixture(tmp_path, recording=_recording([], version="000000000000"))
        report = evaluate(golden)
        assert any("not the current prompts" in w for w in report["warnings"])

    def test_record_requires_live_mode(self, tmp_path):
        with pytest.raises(ValueError, match="--mode live"):
            evaluate(_write_fixture(tmp_path), record=True)


class TestLiveMode:
    def _mock_llm(self, issues):
        llm = MagicMock()
        llm.model = "mock-model"
        llm.with_structured_output.return_value.invoke.return_value = {"issues": issues}
        return llm

    def test_calls_the_provider_with_the_current_prompt(self, tmp_path):
        golden = _write_fixture(tmp_path)
        llm = self._mock_llm([{"severity": "warn", "line": 4, "message": "bare $"}])
        with patch("lyretext.review.agents.create_llm", return_value=llm) as create_llm:
            report = evaluate(golden, mode="live", provider="anthropic")

        create_llm.assert_called_with("anthropic")
        sent = llm.with_structured_output.return_value.invoke.call_args.args[0][0].content
        assert "math notation reviewer" in sent[0]["text"]  # prompts.md, read live
        assert report["metrics"]["by_check"]["math_notation"] == score(1, 0, 1)

    def test_record_writes_a_recording_that_replays_identically(self, tmp_path):
        golden = _write_fixture(tmp_path)
        llm = self._mock_llm([
            {"severity": "warn", "line": 2, "message": "bare $", "suggestion": "<m>x^2</m>"},
            {"severity": "warn", "line": 4, "message": "bare $", "suggestion": "<m>z</m>"},
        ])
        with patch("lyretext.review.agents.create_llm", return_value=llm):
            live = evaluate(golden, mode="live", record=True)

        recording = json.loads((golden / "probe.recorded.json").read_text(encoding="utf-8"))
        assert recording["prompt_version"] == prompt_version()
        assert recording["model"] == "mock-model"
        assert [i["line"] for i in recording["responses"]["math_notation"]] == [2, 4]

        replayed = evaluate(golden)  # recorded mode, no LLM anywhere
        assert replayed["metrics"] == live["metrics"]
        assert replayed["warnings"] == []

    def test_a_fixture_with_only_deterministic_checks_records_nothing(self, tmp_path):
        golden = _write_fixture(tmp_path, checks=["xml_wellformed"], expected=[])
        with patch("lyretext.review.agents.create_llm") as create_llm:
            evaluate(golden, mode="live", record=True)
        create_llm.assert_not_called()
        assert not (golden / "probe.recorded.json").exists()


class TestFixtureValidation:
    def test_an_anchor_off_its_line_is_reported(self, tmp_path):
        golden = _write_fixture(tmp_path, expected=[
            {"label": "x", "check_id": "math_notation", "line": 3, "anchor": "$x^2$"},
        ])
        problems = validate_fixture(load_fixture(golden / "probe.ptx"))
        assert any("anchor" in p for p in problems)

    def test_an_out_of_scope_expectation_is_reported(self, tmp_path):
        golden = _write_fixture(tmp_path, checks=["xml_wellformed"])
        problems = validate_fixture(load_fixture(golden / "probe.ptx"))
        assert any("not in scope" in p for p in problems)

    def test_evaluate_refuses_an_inconsistent_golden_set(self, tmp_path):
        golden = _write_fixture(tmp_path, expected=[
            {"label": "x", "check_id": "math_notation", "line": 99},
        ])
        with pytest.raises(ValueError, match="inconsistent"):
            evaluate(golden)


# ---------------------------------------------------------------------------
# The real golden set
# ---------------------------------------------------------------------------

class TestGoldenSet:
    def test_every_fixture_is_consistent(self):
        fixtures = load_golden_set(DEFAULT_GOLDEN_DIR)
        assert fixtures, "the golden set is empty"
        for fixture in fixtures:
            assert validate_fixture(fixture) == [], fixture.name

    def test_the_deterministic_check_is_perfect_on_it(self):
        """xml_wellformed is a real parse, so this metric is genuine in recorded
        mode: it must find the malformed fixture and nothing anywhere else."""
        report = evaluate(DEFAULT_GOLDEN_DIR)
        assert report["metrics"]["by_check"]["xml_wellformed"]["fp"] == 0
        assert report["metrics"]["by_check"]["xml_wellformed"]["fn"] == 0
        assert report["metrics"]["by_check"]["xml_wellformed"]["tp"] >= 1
