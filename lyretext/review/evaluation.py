"""Review-agent evaluation harness (#32).

Measures the review checks against a curated golden set so that a prompt change
can be judged by numbers rather than by eye: **precision** (of the findings
produced, how many were real) and **recall** (of the real problems, how many
were found), per check and overall, with every false positive and false
negative listed so the prompt can be fixed against them.

Everything here is deterministic except the review pass itself (#37). That pass
runs in one of two modes:

  recorded (default)
      Replays model responses stored beside each fixture
      (``<name>.recorded.json``). Deterministic, free, needs no API key, so it
      runs in CI. It measures the harness and the recordings — **not** the
      current prompts: editing prompts.md changes nothing here until the
      fixtures are re-recorded.
  live
      Calls the real provider with the current prompts.md. This is the mode
      that measures a prompt change. ``--record`` writes its responses back as
      the new recordings, which is how a baseline is captured.

Deterministic checks (``CheckSpec.impl``, e.g. xml_wellformed) run for real in
both modes, so their metrics are genuine either way.

Matching
========
A produced finding matches an expected one when both name the same check and
their line spans lie within ``tolerance`` lines of each other — check prompts
ask for an *approximate* line, so exact line equality would score a correct
finding as one false positive plus one false negative. Matching is one-to-one
and maximises the number of matches, so a problem reported twice scores one
true positive and one false positive: the golden set lists each occurrence it
expects. Two special cases:

  * an expected finding with ``line: null`` is about the document as a whole
    and matches a finding from its check anywhere;
  * a produced finding with no line cannot be credited for a located problem,
    so it only ever matches an unlocated expectation.

Golden-set format
=================
Each fixture is a ``<name>.ptx`` plus ``<name>.expected.json``::

    {
      "description": "what this fixture probes",
      "checks": null,              # or ["xml_wellformed"]: the checks in scope
      "expected": [
        {"label": "bare-inline-dollar", "check_id": "math_notation",
         "line": 5, "end_line": 5, "anchor": "$y = x^2$",
         "severity": "warn", "note": "why this is a real problem"}
      ]
    }

``anchor`` is text that must appear on ``line``; validate_fixture checks it, so
a fixture edited without updating its line numbers fails loudly instead of
quietly scoring the wrong thing.

Run it::

    python -m lyretext.review.evaluation                    # recorded
    python -m lyretext.review.evaluation --mode live        # measure prompts
    python -m lyretext.review.evaluation --mode live --record
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal
from unittest.mock import patch

from . import agents
from .checks import CheckSpec, get_registry
from .grouping import group_key
from .structure import Issue, IssueDraft

Mode = Literal["recorded", "live"]

DEFAULT_GOLDEN_DIR = Path(__file__).resolve().parents[2] / "evals" / "review" / "golden"
DEFAULT_TOLERANCE = 2
DEFAULT_STAGE = "translate"

_DRAFT_FIELDS = tuple(IssueDraft.model_fields)


def prompt_version(prompts_file: Path | None = None) -> str:
    """Content hash of the review prompts — what a run is measured against.

    Line endings are normalised so a Windows and a Linux checkout of the same
    prompts agree on their version.
    """
    path = prompts_file or agents._PROMPTS_FILE
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Golden set
# ---------------------------------------------------------------------------

@dataclass
class Fixture:
    name: str
    path: Path
    artifact: str
    description: str
    # Check ids in scope; None means every check registered for the stage.
    checks: list[str] | None
    expected: list[dict[str, Any]]
    recording: dict[str, Any] | None

    @property
    def recording_path(self) -> Path:
        return self.path.with_name(f"{self.name}.recorded.json")


def load_fixture(ptx_path: Path) -> Fixture:
    name = ptx_path.stem
    expected_path = ptx_path.with_name(f"{name}.expected.json")
    if not expected_path.exists():
        raise ValueError(f"golden fixture {name!r} has no {expected_path.name}")
    spec = json.loads(expected_path.read_text(encoding="utf-8"))
    recording_path = ptx_path.with_name(f"{name}.recorded.json")
    recording = (
        json.loads(recording_path.read_text(encoding="utf-8"))
        if recording_path.exists()
        else None
    )
    return Fixture(
        name=name,
        path=ptx_path,
        # read_text normalises line endings, so line numbers match what the
        # check-agent is shown whatever the checkout did to them.
        artifact=ptx_path.read_text(encoding="utf-8"),
        description=spec.get("description", ""),
        checks=spec.get("checks"),
        expected=list(spec.get("expected", [])),
        recording=recording,
    )


def load_golden_set(
    golden_dir: Path = DEFAULT_GOLDEN_DIR, names: list[str] | None = None
) -> list[Fixture]:
    fixtures = [load_fixture(p) for p in sorted(golden_dir.glob("*.ptx"))]
    if names:
        missing = set(names) - {f.name for f in fixtures}
        if missing:
            raise ValueError(f"no such golden fixture(s): {', '.join(sorted(missing))}")
        fixtures = [f for f in fixtures if f.name in names]
    return fixtures


def validate_fixture(fixture: Fixture, stage: str = DEFAULT_STAGE) -> list[str]:
    """Problems with the fixture itself; an empty list means it is sound."""
    known = {s.id for s in get_registry().get_for_stage(stage)}
    in_scope = set(fixture.checks) if fixture.checks is not None else known
    lines = fixture.artifact.split("\n")
    problems: list[str] = []

    for check_id in sorted(in_scope - known):
        problems.append(f"scope names unknown check {check_id!r}")

    labels: set[str] = set()
    for i, exp in enumerate(fixture.expected):
        where = f"expected[{i}]"
        label = exp.get("label")
        if not label:
            problems.append(f"{where} has no label")
        elif label in labels:
            problems.append(f"{where} repeats label {label!r}")
        labels.add(label)

        check_id = exp.get("check_id")
        if check_id not in in_scope:
            problems.append(f"{where} ({label}) names check {check_id!r}, which is not in scope")

        line = exp.get("line")
        if line is None:
            continue
        end = exp.get("end_line") or line
        if not (1 <= line <= end <= len(lines)):
            problems.append(f"{where} ({label}) span {line}-{end} is outside the document")
            continue
        anchor = exp.get("anchor")
        if anchor and anchor not in lines[line - 1]:
            problems.append(
                f"{where} ({label}) anchor {anchor!r} is not on line {line}: "
                f"{lines[line - 1].strip()!r}"
            )
    return problems


# ---------------------------------------------------------------------------
# Matching and metrics
# ---------------------------------------------------------------------------

def _span(finding: dict[str, Any]) -> tuple[int, int] | None:
    line = finding.get("line")
    if line is None:
        return None
    end = finding.get("end_line") or line
    return (min(line, end), max(line, end))


def _gap(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Lines between two spans; 0 when they overlap."""
    return max(0, max(a[0], b[0]) - min(a[1], b[1]))


def match_findings(
    produced: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    tolerance: int = DEFAULT_TOLERANCE,
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pair produced with expected findings one-to-one.

    Returns ``(matches, false_positives, false_negatives)``: matches as
    ``(produced_index, expected_index)`` pairs, the others as indices of the
    unmatched produced and expected findings respectively.

    A maximum bipartite matching (augmenting paths), so the count of true
    positives never depends on the order findings were reported in. Each
    expected finding tries its candidates nearest-first, which keeps the pairing
    itself sensible as well as the count.
    """
    candidates: list[list[int]] = []
    for exp in expected:
        exp_span = _span(exp)
        scored: list[tuple[int, int]] = []
        for pi, prod in enumerate(produced):
            if prod.get("check_id") != exp.get("check_id"):
                continue
            if exp_span is None:
                scored.append((0, pi))
                continue
            prod_span = _span(prod)
            if prod_span is None:
                continue
            gap = _gap(prod_span, exp_span)
            if gap <= tolerance:
                scored.append((gap, pi))
        candidates.append([pi for _, pi in sorted(scored)])

    owner: dict[int, int] = {}  # produced index -> expected index

    def _assign(ei: int, seen: set[int]) -> bool:
        for pi in candidates[ei]:
            if pi in seen:
                continue
            seen.add(pi)
            if pi not in owner or _assign(owner[pi], seen):
                owner[pi] = ei
                return True
        return False

    for ei in range(len(expected)):
        _assign(ei, set())

    matches = sorted(owner.items(), key=lambda pair: (pair[1], pair[0]))
    matched_expected = set(owner.values())
    false_positives = [pi for pi in range(len(produced)) if pi not in owner]
    false_negatives = [ei for ei in range(len(expected)) if ei not in matched_expected]
    return matches, false_positives, false_negatives


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def score(tp: int, fp: int, fn: int) -> dict[str, Any]:
    """Precision, recall and F1. A ratio with nothing to divide by is None.

    None is deliberate rather than 0 or 1: precision over zero findings, or
    recall over zero expected problems, is undefined, and either default would
    quietly flatter or punish a check that had nothing to do.
    """
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


# ---------------------------------------------------------------------------
# Running the checks
# ---------------------------------------------------------------------------

class _ReplayLLM:
    """Stands in for a chat model, returning one recorded structured response."""

    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self._issues = issues

    def with_structured_output(self, _schema: Any) -> "_ReplayLLM":
        return self

    def invoke(self, _messages: Any) -> dict[str, Any]:
        return {"issues": [dict(issue) for issue in self._issues]}


@contextmanager
def _llm_factory(factory: Callable[..., Any]) -> Iterator[None]:
    """Route run_check's create_llm through *factory* for one check."""
    with patch.object(agents, "create_llm", factory):
        yield


def _draft(issue: Issue) -> dict[str, Any]:
    """The part of an Issue the model produced — what a recording stores."""
    return {f: getattr(issue, f) for f in _DRAFT_FIELDS}


def _finding(issue: Issue) -> dict[str, Any]:
    data = issue.model_dump()
    data["group_key"] = group_key(
        check_id=issue.check_id, message=issue.message, suggestion=issue.suggestion
    )
    return data


def _specs_for(fixture: Fixture, stage: str) -> list[CheckSpec]:
    specs = get_registry().get_for_stage(stage)
    if fixture.checks is not None:
        specs = [s for s in specs if s.id in fixture.checks]
    return specs


def run_fixture(
    fixture: Fixture,
    *,
    mode: Mode = "recorded",
    provider: str = "gemini",
    tolerance: int = DEFAULT_TOLERANCE,
    stage: str = DEFAULT_STAGE,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the in-scope checks over one fixture and score them.

    Returns ``(result, recording)``: the scored result for the report, and —
    in live mode — what a ``--record`` would write for this fixture.
    """
    state = {"artifact": fixture.artifact, "chapter_id": fixture.name, "provider": provider}
    recorded_responses = (fixture.recording or {}).get("responses", {})

    produced: list[dict[str, Any]] = []
    checks_run: list[str] = []
    unrecorded: list[str] = []
    responses: dict[str, list[dict[str, Any]]] = {}
    models: set[str] = set()

    for spec in _specs_for(fixture, stage):
        if spec.impl is not None:
            issues = spec.impl(state, None).get("issues", [])
        elif mode == "recorded":
            if spec.id not in recorded_responses:
                unrecorded.append(spec.id)
                continue
            replay = _ReplayLLM(recorded_responses[spec.id])
            with _llm_factory(lambda *_a, **_k: replay):
                issues = agents.run_check(spec)(state, None).get("issues", [])
        else:
            real_create_llm = agents.create_llm

            def _live(*args: Any, **kwargs: Any) -> Any:
                llm = real_create_llm(*args, **kwargs)
                model = getattr(llm, "model", None) or getattr(llm, "model_name", None)
                if model:
                    models.add(str(model))
                return llm

            with _llm_factory(_live):
                issues = agents.run_check(spec)(state, None).get("issues", [])
            responses[spec.id] = [_draft(i) for i in issues]

        checks_run.append(spec.id)
        produced.extend(_finding(i) for i in issues)

    # Expectations for a check that did not run say nothing about it this time.
    expected = [e for e in fixture.expected if e.get("check_id") in checks_run]
    matches, fp_idx, fn_idx = match_findings(produced, expected, tolerance)

    counts = {c: {"tp": 0, "fp": 0, "fn": 0} for c in checks_run}
    for pi, ei in matches:
        counts[expected[ei]["check_id"]]["tp"] += 1
    for pi in fp_idx:
        counts[produced[pi]["check_id"]]["fp"] += 1
    for ei in fn_idx:
        counts[expected[ei]["check_id"]]["fn"] += 1

    recording_meta = None
    if fixture.recording is not None:
        recording_meta = {
            k: fixture.recording.get(k)
            for k in ("prompt_version", "provider", "model", "recorded_at")
        }

    result = {
        "fixture": fixture.name,
        "checks_run": checks_run,
        "unrecorded_checks": unrecorded,
        "recording": recording_meta,
        "counts": counts,
        "matched": [
            {
                "label": expected[ei].get("label"),
                "check_id": expected[ei]["check_id"],
                "expected_line": expected[ei].get("line"),
                "produced_line": produced[pi].get("line"),
                "message": produced[pi].get("message"),
            }
            for pi, ei in matches
        ],
        "false_positives": [
            {k: produced[pi].get(k) for k in ("check_id", "line", "end_line", "severity", "message", "suggestion")}
            for pi in fp_idx
        ],
        "false_negatives": [
            {k: expected[ei].get(k) for k in ("label", "check_id", "line", "end_line", "note")}
            for ei in fn_idx
        ],
    }
    recording = {
        "provider": provider,
        "model": ", ".join(sorted(models)) or None,
        "responses": responses,
    }
    return result, recording


def evaluate(
    golden_dir: Path = DEFAULT_GOLDEN_DIR,
    *,
    mode: Mode = "recorded",
    provider: str = "gemini",
    tolerance: int = DEFAULT_TOLERANCE,
    stage: str = DEFAULT_STAGE,
    fixtures: list[str] | None = None,
    record: bool = False,
    label: str | None = None,
) -> dict[str, Any]:
    """Run the golden set and return the report (see format_report)."""
    if record and mode != "live":
        raise ValueError("--record needs --mode live: only a live run has responses to record")

    version = prompt_version()
    golden = load_golden_set(golden_dir, fixtures)

    problems = {f.name: validate_fixture(f, stage) for f in golden}
    problems = {name: ps for name, ps in problems.items() if ps}
    if problems:
        detail = "; ".join(f"{n}: {', '.join(ps)}" for n, ps in problems.items())
        raise ValueError(f"golden set is inconsistent: {detail}")

    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    totals: dict[str, dict[str, int]] = {}
    recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for fixture in golden:
        result, recording = run_fixture(
            fixture, mode=mode, provider=provider, tolerance=tolerance, stage=stage
        )
        results.append(result)
        for check_id, c in result["counts"].items():
            t = totals.setdefault(check_id, {"tp": 0, "fp": 0, "fn": 0})
            for k in t:
                t[k] += c[k]

        if mode == "recorded":
            if result["unrecorded_checks"]:
                warnings.append(
                    f"{fixture.name}: no recording for {', '.join(result['unrecorded_checks'])} "
                    f"- excluded from the metrics (run --mode live --record to capture one)"
                )
            rec = result["recording"]
            if rec and rec.get("prompt_version") != version:
                warnings.append(
                    f"{fixture.name}: recorded with prompt version {rec.get('prompt_version')}, "
                    f"current prompts are {version}; its LLM metrics describe the recording, "
                    f"not the current prompts"
                )
        elif record:
            fixture.recording_path.write_text(
                json.dumps(
                    {"prompt_version": version, "recorded_at": recorded_at, **recording},
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

    by_check = {c: score(**t) for c, t in sorted(totals.items())}
    overall = score(
        sum(t["tp"] for t in totals.values()),
        sum(t["fp"] for t in totals.values()),
        sum(t["fn"] for t in totals.values()),
    )
    return {
        "mode": mode,
        "prompt_version": version,
        "label": label,
        "provider": provider if mode == "live" else None,
        "tolerance": tolerance,
        "stage": stage,
        "run_at": recorded_at,
        "metrics": {"overall": overall, "by_check": by_check},
        "fixtures": results,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Reporting / CLI
# ---------------------------------------------------------------------------

def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def format_report(report: dict[str, Any]) -> str:
    head = (
        f"Review eval - mode={report['mode']}  prompts={report['prompt_version']}  "
        f"tolerance=+/-{report['tolerance']}"
    )
    if report.get("label"):
        head += f"  label={report['label']}"
    if report.get("provider"):
        head += f"  provider={report['provider']}"

    rows = [("check", "TP", "FP", "FN", "precision", "recall", "F1")]
    for check_id, m in report["metrics"]["by_check"].items():
        rows.append((check_id, m["tp"], m["fp"], m["fn"], _fmt(m["precision"]), _fmt(m["recall"]), _fmt(m["f1"])))
    o = report["metrics"]["overall"]
    rows.append(("overall", o["tp"], o["fp"], o["fn"], _fmt(o["precision"]), _fmt(o["recall"]), _fmt(o["f1"])))
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    table = [
        "  ".join(str(cell).ljust(w) if i == 0 else str(cell).rjust(w) for i, (cell, w) in enumerate(zip(row, widths)))
        for row in rows
    ]

    out = [head, "", *table]

    fps = [(r["fixture"], fp) for r in report["fixtures"] for fp in r["false_positives"]]
    if fps:
        out += ["", "False positives (reported, not expected):"]
        out += [f"  {name}  {fp['check_id']}  L{fp.get('line')}  {fp.get('message')}" for name, fp in fps]
    fns = [(r["fixture"], fn) for r in report["fixtures"] for fn in r["false_negatives"]]
    if fns:
        out += ["", "False negatives (expected, not reported):"]
        out += [f"  {name}  {fn['check_id']}  L{fn.get('line')}  {fn.get('label')}" for name, fn in fns]
    if report["mode"] == "recorded":
        out += [
            "",
            "Note: recorded mode replays stored responses; it does not measure the "
            "current prompts. Use --mode live for that.",
        ]
    if report["warnings"]:
        out += ["", "Warnings:"] + [f"  {w}" for w in report["warnings"]]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m lyretext.review.evaluation",
        description="Measure the review checks against the golden set (precision/recall).",
    )
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN_DIR, help="golden-set directory")
    parser.add_argument("--mode", choices=["recorded", "live"], default="recorded")
    parser.add_argument("--provider", choices=["gemini", "anthropic"], default="gemini", help="live mode only")
    parser.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE, help="line window for a match")
    parser.add_argument("--stage", default=DEFAULT_STAGE)
    parser.add_argument("--fixture", action="append", dest="fixtures", help="run only this fixture (repeatable)")
    parser.add_argument("--record", action="store_true", help="live mode: save responses as the new recordings")
    parser.add_argument("--label", help="human name for this prompt version, carried into the report")
    parser.add_argument("--out", type=Path, help="also write the full report as JSON here")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    try:
        report = evaluate(
            args.golden,
            mode=args.mode,
            provider=args.provider,
            tolerance=args.tolerance,
            stage=args.stage,
            fixtures=args.fixtures,
            record=args.record,
            label=args.label,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(format_report(report))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nReport written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
