# Review-agent golden set

Curated PreTeXt fixtures with known expected findings, used by
`lyretext/review/evaluation.py` (#32) to measure the review checks' **precision**
(of the findings reported, how many were real) and **recall** (of the real
problems, how many were reported), so prompt changes can be measured rather than
guessed.

## Running

```bash
# Recorded (default): replays stored model responses. Deterministic, free, CI-safe.
python -m lyretext.review.evaluation

# Live: calls the provider with the current prompts.md. THIS is what measures a prompt change.
python -m lyretext.review.evaluation --mode live --provider gemini --label "stricter math rule"

# Live, and save the responses as the new recordings (the new baseline).
python -m lyretext.review.evaluation --mode live --record

# Options: --fixture NAME (repeatable), --tolerance N, --out report.json
```

**Recorded mode does not measure the current prompts.** It replays what a model
said when the fixture was recorded, so editing `prompts.md` changes nothing there
until you re-record. The report warns when a recording was made under a different
prompt version (a hash of `prompts.md`), and lists any LLM check with no recording
at all — those are excluded from the metrics, not counted as misses. Deterministic
checks (`xml_wellformed`) always run for real, so their numbers are genuine in
both modes.

The committed recordings are the first live baseline: `gemini-3.1-flash-lite`,
prompt version `b400d85fc6e0`, recorded 2026-10-12. On that baseline, both
LLM checks found every expected problem (recall 1.0) and each produced one
false positive (precision 0.8). Fixtures scoped only to deterministic checks
(`malformed_math`) have no recording, because there is no model output to store.

## Fixture format

Each fixture is a `<name>.ptx` plus a `<name>.expected.json`, and optionally a
`<name>.recorded.json` written by `--record` (do not hand-edit those — they are
meant to be what a model actually said).

```json
{
  "description": "what this fixture probes, and why",
  "checks": null,
  "expected": [
    {
      "label": "bare-inline-dollar",
      "check_id": "math_notation",
      "line": 5,
      "end_line": 5,
      "anchor": "$y = x^2$",
      "severity": "warn",
      "note": "why this is a real problem"
    }
  ]
}
```

- `checks` — the checks in scope; `null` means every check registered for the
  stage. Findings from out-of-scope checks are not scored. A fixture with
  `"expected": []` and `"checks": null` is a pure false-positive probe.
- `line` / `end_line` — 1-based, as the check-agent sees them. `line: null` means
  a whole-document finding that matches anywhere.
- `anchor` — text that must appear on `line`. The harness refuses to run if it
  does not, so a fixture edited without fixing its line numbers fails loudly.
- List **every occurrence** you expect: matching is one-to-one, so a problem the
  model reports twice scores one hit and one false positive.

## How a finding is scored

A produced finding matches an expected one when both name the same check and
their line spans are within `--tolerance` lines (default 2; prompts ask models
for an *approximate* line). Matching is a maximum one-to-one pairing, nearest
first. Unmatched produced findings are **false positives**, and unmatched expected
findings are **false negatives**. Both are listed in the report by line, so the
prompt can be fixed against them.

`precision = TP / (TP + FP)`, `recall = TP / (TP + FN)`. A ratio with a zero
denominator is reported as `n/a` rather than 0 or 1, because silence is not
perfect precision.

## Adding a fixture

Prefer small fixtures that each probe one thing, plus real chapters when a
failure was seen in the wild (cut the chapter down to the region that matters).
After adding one, run the harness once in recorded mode: it validates every
fixture's anchors and scope before scoring anything.
