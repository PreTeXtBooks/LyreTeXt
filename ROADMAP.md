# LyreTeXt roadmap

Waves greenlight work. Only issues in the **lowest open wave** — minus any milestone whose
description starts with `HOLD:` — are claimable; later waves are recorded backlog. A wave
closes when all its milestones close, which greenlights the next. Milestones are
`W<wave>-<Stream>`; the streams and their prefixes are defined in Part 1 of `CLAUDE.md`.
Decisions (`decision` label) are not milestoned and may be raised in any wave.

## Wave 1 — Human review & edit loop  *(active)*

Get the human-in-the-loop review/edit experience solid. Ties to decision #12 (the human
decides what gets fixed) and the intended gate interaction model.

**Live streams:** UI, PIPE, CORE, DESK.

- **W1-UI** — #13 input validation warnings, #14 preview-pane scroll reset, #15 workspace
  gate behaviour + terminology, #25 bundle IBM Plex Mono locally.
- **W1-PIPE** — #16 line-wise review findings + terminal output, #17 edit UX (grouping,
  accept + apply), #18 investigate current auto-edit handling.
- **W1-CORE** — #62 adopt inferenceswitch for LLM dispatch (priority; decision #61).
- **W1-DESK** — Windows desktop distribution: Electron shell + Python sidecar (decision #63;
  Windows first #64, installed R for Rmd #65, unsigned beta #66). #68 Phase 0 spike →
  #67 local API-key handling (the core motivation) and #69 backend desktop-readiness (after
  #62 Phase 1) → #70 bundle pandoc/`pretext.lua` + R detection → #71 Windows installer + CI.
  Later: #72 signing, auto-update, macOS/Linux. Managed by Claude under delegation (see
  `CLAUDE.md` Part 1). *Done when* an unsigned Windows beta installer is published that runs
  LaTeX (and Rmd where R is installed) with the user's own API keys held locally.

**Closes when:** a run reaches the per-chapter review gate; findings display line-wise;
accept / apply / retry edits work in the workspace; and gate language is clear and consistent.

## Wave 2 — Pipeline breadth & platform  *(backlog until Wave 1 closes)*

- **W2-PIPE** — #19 LaTeX (`.tex`) input pipeline. *Needs a scope line before it is claimed.*
- **W2-ORCH** — #20 correct the concurrency-spec expectation, #21 reframe the workflow-fabric
  doc. Both bear on the open architecture decision #11.
- **W2-CORE** — #24 CLI polish (cross-platform paths, import hygiene, `--run-id`, real
  streaming).

**Closes when:** the LaTeX input pipeline works end-to-end; the CLI is cross-platform and
polished; and the ORCH design docs are reconciled with the resolved decision #11.

## Future waves

Unplanned. Add new streams/waves here as scope is agreed, and file issues against a wave's
milestones as soon as the work is foreseeable — even while that wave is still backlog.
