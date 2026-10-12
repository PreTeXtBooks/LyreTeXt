# LyreTeXt — System Specification & Holistic Test Brief

This document describes what LyreTeXt is, how a run flows end to end, what each surface
does, what is implemented vs. not, and a structured plan for a **holistic review and test
of the whole system**. It is written for an independent agent doing that review. It is a
functional spec — it deliberately does not restate the private design philosophy in the
local `development/`/`docs/` notes.

> **Verify, don't assume.** Where this spec states behaviour, confirm it against the code
> and a live run before reporting. Where it is vague, treat that as a prompt to read the
> relevant module, not to guess.

---

## 1. What the system is

LyreTeXt turns textbook source (LaTeX `.tex`, R Markdown `.Rmd`; Quarto `.qmd` planned)
into **PreTeXt**, through a pipeline of agent (LangGraph) stages with a human-in-the-loop
review/edit surface driven from a web frontend.

**The load-bearing distinction:** the per-stage LangGraph pipelines are *not* the system of
record. LangGraph is the **topology** of each stage; the orchestration layer above it
(`lyretext/orchestration/` + `service.py`/`viewmodel.py`) owns run state, checkpointing and
the HITL gates (decision #11). A guiding principle throughout is **deterministic where
possible; AI only where it adds genuine value** (decision #37) — mechanical work (parsing,
structure extraction, manifest assembly, finding reconciliation) is deterministic; the LLM
is reserved for translation, review judgement, and ambiguous classification.

**Concurrency model (important for testing):** once a run's manifest is approved, each
chapter runs as its **own graph invocation on its own checkpoint thread** — not as branches
of one graph. This is deliberate: LangGraph's per-thread superstep gating is all-or-nothing
under HITL, so independent per-chapter progress requires one `thread_id` per chapter
(decision #11). The test plan must exercise genuinely concurrent, independently-resolved
chapters.

---

## 2. Run lifecycle (end to end)

1. **Ingest** — user starts a run from a local folder path or a client upload (staged into
   a working project dir). `POST /api/runs` or `POST /api/runs/upload`.
2. **Read / skeleton** (`lyretext/read/`) — detect project type and source files, compile to
   an intermediate form where needed (Rmd → Markdown via knitr; LaTeX goes straight to
   pandoc later), and produce a **manifest** of chapters (+ front/back matter). The
   chapter-structure step is agentic (`read/agents.py::structure_project`); the LaTeX
   chapter list is derived deterministically from top-level `\include`/`\input` in the main
   file's body (`pipeline/tex.py`).
3. **Manifest (read→translate) gate** — a mandatory human checkpoint
   (`orchestration/graph.py::evaluate_read_stage_gate`). Actions: `approve_continue`,
   `edit_manifest`, `select_chapters`, `skip_chapters`, `recompile_chapters`, `abort_run`,
   `manual_file_edit_acknowledged`. No chapter work starts until this passes.
4. **Per-chapter work** (each on its own thread — `invoke_chapter_graph`/
   `resume_chapter_graph`): **translate** (`lyretext/translate/`; LaTeX runs pandoc once over
   the whole project with the pretext.lua writer, then `convert/split.py` splits the
   monolithic PreTeXt per chapter by order-correlation) → **review** (`lyretext/review/`;
   produces line-anchored findings, reconciled across re-runs — additive, auto-resolve
   fixed, persist ignored, per #33) → **edit** (`lyretext/edit/`; apply/retry/edit at the
   review gate).
5. **Review/edit gate** (per chapter) — the core HITL interaction. Three distinct actions
   must always be available: **accept as-is**, **retry with instructions**, **edit
   directly** (editing source → recompile; editing output → re-validate). Auto-retry only
   up to a low limit, then control passes to the human.
6. **Enhance** — *scaffolded but NOT implemented* (`enhance/agents.py` raises
   `NotImplementedError`; scope is open decision #29).
7. **Format / render / output** — `format/` (pretext_fmt, repair), `render/`
   (pretext_html), and assembly of a unifying **`main.ptx`** that `<xi:include>`s the parts
   into one compilable project (#34). Output is browsable and downloadable
   (`/api/runs/{id}/output`, `/project.zip`).
8. **Checkpoint & resume** — every gate boundary is checkpointed; a run can be left and
   resumed. Per-chapter checkpoints support revert (backend exists; **UI not yet wired** —
   #27).

---

## 3. Frontend surfaces (`frontend/app.js`) and their endpoints

| Surface | What it does | Key endpoints |
|---|---|---|
| **Dashboard / New run** | start a run (path/upload); "recent + see all" resume teaser | `POST /api/runs`, `/upload` |
| **Runs library** (#47) | view/open all runs — list, sort, filter, search, open/resume | `GET /api/runs` (summary) |
| **Manifest gate** | review detected structure; approve/edit/select/skip/abort | `POST /api/runs/{id}/gate`, `/chapters/decide` |
| **Chapter landing** | per-chapter progress; run next stage; run all / run selected; remap | `/chapters/commands`, `/remap` |
| **Review/edit workspace** | source / translated / PreTeXt panes; findings (line-anchored); 3 gate actions; checkpoints (stub) | `/findings`, `/issues/*`, `/render`, `/file`, `/checkpoints`, `/revert` |
| **Jobs** | per-chapter + overall status, per-step timing | `GET /api/runs/{id}/jobs`, `/status` |
| **Output** | preview + download final PreTeXt incl. `main.ptx` | `/output`, `/output/{file}`, `/project.zip` |
| **Settings** | global + per-node LLM provider/model overrides | `GET`/`PATCH /api/settings` |

---

## 4. Implementation status (so known gaps aren't reported as bugs)

**Working:** LaTeX input incl. `\input` structuring (#30); Rmd input; read→manifest gate;
per-chapter concurrent translate/review/edit; line-anchored review findings + web display
(#16); stateful review reconciliation (#33); `main.ptx` assembly (#34); runs library MVP
(#47); settings per-node overrides; checkpoint/resume; mock mode (`LYRETEXT_MOCK=1` swaps in
`orchestration/mock_graph.py` — no LLM calls, good for flow testing).

**Known gaps / deferred (do NOT file as new bugs — they're tracked):**
- Enhance stage unimplemented — #29 (design open).
- Quarto `.qmd` pipeline is a stub — #28.
- Review findings in the **terminal/CLI** — #46 (web display is done).
- Checkpoint history/revert **UI** not wired (backend ready) — #27.
- Red/yellow **input validation** states — #13.
- `.sty`/user-macro handling in LaTeX→PreTeXt — #31.
- Nested project-structure (tree manifest) model — #38.

**Known open bug to confirm/characterise:** the selected pipeline isn't carried through the
whole app — e.g. the edit-chapter screen labels source `.rmd` even on the LaTeX pipeline
(#55).

**In progress:** review-agent evaluation harness (#32).

---

## 5. Holistic test plan

Run these as scenarios; for each, note expected vs. actual with evidence. Prefer real
sample projects under `examples/`/`demo/`; use `LYRETEXT_MOCK=1` to exercise flow/UI without
LLM cost, and at least one non-mock run per input format for real behaviour. The Python
suite (`.venv/Scripts/python.exe -m pytest -q`) should be green as a baseline.

**A. End-to-end happy paths**
- A1. LaTeX project (`\include`-structured) → ingest → manifest gate → translate → review →
  edit → output; download `project.zip`; confirm `main.ptx` assembles the chapters in order
  and the project is compilable PreTeXt.
- A2. LaTeX project **`\input`-structured** (and a preamble `\input{macros}`) → confirm
  chapters detected correctly and the preamble input is *not* a chapter (#30).
- A3. Rmd/bookdown project → knitr compile → same flow.

**B. Concurrency & gates**
- B1. Run several chapters at once; confirm they progress and can be resolved
  **independently** (approving one chapter's review does not block others).
- B2. Manifest gate: exercise every action (approve / edit / select / skip / recompile /
  abort).
- B3. Review/edit gate: exercise all three actions (accept / retry-with-instructions /
  edit-directly, source and output edits), and the auto-retry→human escalation.

**C. Review quality & consistency**
- C1. Re-run review after fixing some findings: confirm the set is **additive** (prior
  findings carried), **fixed findings auto-resolve**, and **ignored stay ignored** (#33).
- C2. Sanity-check findings are line-anchored and survive edits that shift lines (#16).

**D. State, output, settings**
- D1. Leave and resume a run from a checkpoint mid-review.
- D2. Runs library: list/sort/filter/search/open/resume across several runs (#47).
- D3. Settings: set a per-node model override; confirm it takes effect on a new run and not
  retroactively.
- D4. Output preview shows real file content; download is complete.

**E. Cross-cutting checks**
- E1. **Pipeline propagation** — confirm/characterise #55: trace the pipeline value through
  ingest → state → view model → every UI surface; list every place a format is hardcoded or
  mis-labelled.
- E2. Error handling — missing R/knitr or pandoc, a malformed main file, an empty project:
  confirm actionable diagnostics, not silent failure.

---

## 6. How to report

This is a **read-only review/test pass**. Per the repo's collaboration protocol
(`CLAUDE.md`): do **not** fix code directly — surface findings as the deliverable. For each
confirmed defect, file a GitHub issue (or comment on the existing one — check the numbers
above first) with repro steps and evidence; group them in a summary comment/report ranked by
severity. Flag anything that contradicts this spec so the spec can be corrected. Distinguish
**confirmed bugs** from **known gaps** (§4) and from **design questions** (open decisions
#29/#31/#38). Do not close issues or change milestones — that's the maintainer's call.
