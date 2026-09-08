# LyreTeXt — Claude Working Instructions

This document has two parts.

**Part 1** is specific to this repository: what it is, where things live, which streams
work is divided into, the repo's overrides, and what still needs setting up. **Part 2** is
the shared IDEMS collaboration protocol
([IDEMSInternational/collab-protocol](https://github.com/IDEMSInternational/collab-protocol)),
carried verbatim by every repo that uses it and delimited by `BEGIN/END SHARED PROTOCOL`
markers. Do not edit Part 2 to suit this repo — if it needs to change, it needs to change
everywhere. Repo-specific facts belong in Part 1, and Part 2 points at them rather than
naming them.

---

# Part 1 — This Repository

## Project Summary

LyreTeXt is an agentic system for creating and maintaining **PreTeXt** versions of
textbooks. Source material (LaTeX/`.tex`, R Markdown/`.Rmd`, Quarto/`.qmd`) is read,
converted, translated into PreTeXt, structurally enhanced, formatted, rendered, and
reviewed — each stage an agent (LangGraph) pipeline — with a human-in-the-loop review and
edit surface driven from a web frontend.

**The boundary a newcomer most often gets wrong:** the per-stage pipelines are *not* the
system of record. LangGraph is the **topology** of each stage; the durable orchestration
layer above it (`orchestration/` plus `service.py`/`viewmodel.py`) is what owns run state,
checkpointing, and the HITL gates. When reasoning about "where does progress live," the
answer is the orchestration fabric, not a graph's checkpoint. The direction this is heading —
generalising the checkpointing/HITL machinery into a domain-neutral durable workflow fabric —
is worked out in local design notes under `development/` and `docs/`, which are **not tracked
in the repo** (see the repository-map note); treat them as private working material.

**Companion repo:** none.

## Repository map

Files marked **hub** are shared surfaces that many streams register into. Part 2's
conflict-avoidance rules read this marking: prefer not to run two tasks that both edit the
same hub file concurrently; new files essentially never conflict.

| Path | What it is |
|---|---|
| `lyretext/` | The Python package. |
| `lyretext/config.py` | **hub** — configuration surface. |
| `lyretext/api.py` | **hub** — HTTP / programmatic API surface. |
| `lyretext/service.py` | **hub** — service layer above the graphs; owns run orchestration. |
| `lyretext/viewmodel.py` | **hub** — view model consumed by the frontend. |
| `lyretext/cli.py` | CLI entrypoint. |
| `lyretext/orchestration/` | Orchestration fabric: `graph.py` **hub**, `state.py` **hub**, `checkpointing.py`, `validation.py`, `mock_graph.py`. |
| `lyretext/pipeline/` | Input-format pipelines: `tex.py`, `rmd.py`, `qmd.py`. |
| `lyretext/read/` `convert/` `translate/` `enhance/` `format/` `render/` `review/` `edit/` | Per-stage agent/tool packages; **structure varies by stage** — agentic stages (`read`, `translate`, `enhance`, `review`) carry `agents.py`/`graph.py`/`state.py`/`structure.py`/`prompts/`, while others are plain modules (`convert/`: `pandoc.py`, `split.py`; `format/`: `pretext_fmt.py`, `repair.py`; `render/`: `pretext_html.py`; `edit/`: `agents.py` + `prompts/`). Stage-local, not hubs. |
| `lyretext/prompts/`, `lyretext/utils/` | Shared prompt(s) and utilities (`filesystem`, `prompts`, `text`). |
| `frontend/` | Web UI: `index.html`, `app.js`, `api-client.js` **hub**, `styles.css`, `assets/`. |
| `tests/` | Test suite. |
| `docs/` | **Local, not tracked** (gitignored). Design + experiment writeups (`architecture/`, `experiments/`), kept private for now — not shared state. |
| `development/` | **Local, not tracked** (gitignored). Working design notes, issue drafts, and UI mocks — scratch, private, not authoritative. |
| `examples/`, `demo/`, `archive/` | Example projects, demo assets, archived material. |
| `config.yml` | **hub** — runtime config. |
| `requirements.txt` | **hub** — dependencies. |
| `.gitignore` | **hub**. |
| `CLAUDE.md` | **hub** — this file (Part 1 + Part 2). |

## Streams

Four streams, cut so two rarely touch the same hub file. The cut is about conflict surface
and is provisional — revise it if the real contention shows up elsewhere.

| Stream | Issue prefix | Concern |
|---|---|---|
| Pipeline / Agents | `PIPE` | The per-stage agent packages (`read`…`edit`) and `pipeline/` input formats. Mostly additive, low hub contention. |
| Orchestration | `ORCH` | `orchestration/*` — the graph, checkpointing, HITL workflow fabric, shared run state. |
| Frontend | `UI` | `frontend/*`. |
| Core / Platform | `CORE` | `config.py`, `api.py`, `service.py`, `cli.py`, `viewmodel.py` — the shared service/API/CLI surface, isolated so it serialises. |

## Repo-specific bindings

| Setting | Value |
|---|---|
| **Worktree root** | `.claude/worktrees/` (gitignored; `.claude/resume/` is **not** ignored) |
| **Roadmap document** | `ROADMAP.md` |
| **Decision archive** | none (no pre-migration decision file) |
| **Granularity experiment** | **inactive** |

## Repo-specific overrides

### Subagents are human-launched and human-gated
*Overrides Part 2 — "Deciding without asking → Autonomous session", and the spawned-agent
assumption in "Worktrees".*

This repo does not run unattended autonomous agents, **and the main session does not
author source code.** All source-code changes — creating or editing files under
`lyretext/`, `frontend/`, `tests/`, or any other code path — are made by a **gated
subagent**, never by the main session directly. The main session plans, investigates,
runs and reads tests, manages issues / PRs / decision records, and edits **coordination
docs only** (this file, `ROADMAP.md`, `.gitignore`, `.claude/resume/`) — it does not write
or edit code. When code needs writing, spawn a chip (below); do not do it inline. Then:

1. **Spawn subagents as spawn-task chips, not auto-launched Agent or background calls.**
   Surface the work as a chip the maintainer clicks to launch; the maintainer decides when
   it runs. An in-process or background agent started without a chip is not how work is
   delegated here.
2. **A spawned agent does not start work automatically.** Every chip's prompt instructs the
   agent to gather context and present its plan (and any decision options, in Part 2's
   format), then **stop and wait for the maintainer's approval before making any change** —
   claiming an issue, pushing, editing, or merging.
3. **Review agents post their review as a comment on the reviewed PR.** That comment is the
   review's deliverable and its durable home (GitHub is the only shared state) — not merely a
   report back into the spawning session, which disappears when the session ends. Mark it
   clearly as agent-generated (e.g. a `🤖 Automated review` heading). Having posted, the agent
   stops: it does not merge, push, edit, or file a formal GitHub approval / change-request.
   Whether to act on the review is the maintainer's decision.

**Consequence for Part 2:** the *"Autonomous session — nobody is going to answer"* branch of
the decision protocol does **not** apply in this repo. Because a human is always in the loop
before a spawned agent acts, agents follow the *interactive, present-and-wait* rules at all
tiers and never decide-close-and-flag `unreviewed` on their own initiative. `unreviewed`
therefore only arises from in-session `[IMPL]` calls the maintainer is shown in the batch
summary — the same as any interactive session.

---

## Migration status

**Adoption is phased. Phases 0–2 are done — the task/claim *mechanics* (milestones/waves,
issue-as-lock) are now in force: the active, claimable set is Wave 1 (`W1-UI`, `W1-PIPE`),
and everything in Wave 2 is recorded backlog until Wave 1 closes (see `ROADMAP.md`). Phase 3
remains deferred.** Design intent lives in local, untracked notes (`development/`, `docs/`);
treat those as private working material, not shared state. Do not silently work around this
list — if a task needs something on it, that need is the reason to do the setup item.

**Done**
- [x] Part 1 written; Part 2 embedded verbatim.
- [x] Worktree root gitignored; `.claude/resume/` kept tracked.
- [x] The 10 protocol labels created (plus a `backlog` label for pre-Phase-2 items).
- [x] Standing decisions filed: #11 `[ARCH]` workflow-fabric ↔ LangGraph; #12 `[DESIGN]`
      human decides what gets fixed (open — `[DESIGN]` is human-closed).
- [x] Phase 0 — branch consolidation: orchestration (#10) and frontend/CLI/tests + adoption
      (#22) merged to `main`; stale `dev-*` / `refactor` / `upgrades` / `ui-prototype`
      branches pruned.
- [x] Outstanding TODOs captured as `backlog` issues #13–#21 (from `frontend/ISSUES.md`,
      now retired).

**Phase 2 — roadmap + milestones (done)**
- [x] Roadmap written (`ROADMAP.md`) and named in the bindings table above.
- [x] Milestones created: `W1-UI`, `W1-PIPE` (active) and `W2-PIPE`, `W2-ORCH`, `W2-CORE`
      (backlog).
- [x] Backlog issues #13–#21, #24–#25 converted to claimable `task`s on their milestones
      (the `backlog` label is now unused).

**Phase 3 — deferred until work is genuinely parallel**
- [ ] Worktrees, task-suspension mechanics, and the instrumentation/granularity experiment
      (kept **inactive**).

---

<!-- BEGIN SHARED PROTOCOL v1 -->
<!--
  Everything between these markers is shared verbatim across repos using this protocol.
  It must contain NO repo-specific names — no file paths, no stream names, no milestone
  titles. Where it needs one, it points at Part 1 of the host document instead.

  To check for drift, extract the block and compare across repos:
    awk '/^<!-- BEGIN SHARED/,/^<!-- END SHARED/' <repo>/CLAUDE.md | md5sum
  The patterns are anchored at line start (^) on purpose: this very comment mentions the
  marker names, and an unanchored pattern matches here and truncates the block. Any
  difference in the hash is either a deliberate upgrade that has not been propagated, or
  an accidental edit. Both are worth seeing.
-->

# Part 2 — Shared Protocol

## Collaboration Model

Many people work this repo on largely non-overlapping hours. **GitHub is the only shared
state.** Anything that needs to be visible to the other person must live in an issue, a
PR, or a commit message — never in a local file, a session, or an untracked note.

Three artifacts carry all coordination:

| Artifact | Carries | Conflict risk |
|---|---|---|
| **Issue** (`decision` label) | Design/architecture decisions — proposed and settled | None |
| **Issue** (`task` label) | Units of work; assignment is the lock | None |
| **Draft PR** | Claim on a task + live handoff state + review surface | None (one per branch) |

Issues and PR bodies never merge-conflict. That is the point.

---

# Decision Handling Protocol

## Decision tiers

Three tiers categorize decisions by the level of abstraction at which they operate. The
tier appears **both** as a label and as a `[TIER]` prefix in the issue title.

- **`[DESIGN]`** — Abstract design philosophy decisions about what properties the system's
  representations and behaviors should have, and why those properties matter for the
  system's goals. These address questions such as: what information should be treated as
  signal versus noise for the downstream task, what aspects of computation should be
  observable or opaque, what criteria define a good output, and what tradeoffs exist
  between expressiveness and simplicity. DESIGN decisions should be legible to a domain
  expert without software implementation context. In a DESIGN record, **Problem** describes
  the abstract tension or question being resolved; **Related Decisions** uses the label
  `Motivates: #N` to link forward to the ARCH/IMPL issues this principle informs.

- **`[ARCH]`** — Decisions that affect system shape, data models, pipeline stage boundaries,
  or module structure. These give concrete form to a DESIGN principle. Most ARCH decisions
  should cite a `[DESIGN]` issue that motivates them. If an ARCH decision has no natural
  DESIGN parent, note this explicitly in the record and ask whether a DESIGN issue should
  be opened — this tracing gap is a signal that the underlying design philosophy hasn't
  been articulated yet.

- **`[IMPL]`** — Implementation-level forks where both options satisfy the architecture.
  No DESIGN citation required.

**The tier is a provisional gate, and it is under measurement.** Tier currently decides how
much consultation a decision earns, but there is reason to think it is the wrong predictor —
see *The granularity question* at the end of this part. Follow the rules below; record the
data; expect the thresholds to move.

## Decision records are GitHub issues

Each decision is one issue.

- **The issue number is the decision ID.** Never invent a `DECISION-NNN` scheme. GitHub
  numbers are globally unique, stable, and collision-free across branches and people.
- **Cross-references use `#N`.** GitHub renders these as bidirectional backlinks, so
  `Motivates:` / `Motivated by:` traceability is free and always current.
- **Cross-repo references use `owner/repo#N`** and work identically. Use them; a decision
  in one repo that binds another must be citable from both sides.
- **Issue state is decision status:**
  - **Open** = proposed, under discussion, or awaiting the other person's input.
  - **Closed** = settled. The record stands as written.
  - **Superseded** = reopen, add the `superseded` label, comment with a forward link to the
    replacing issue, close again. Never edit the original decision's body to reverse it —
    the rejected path is the most valuable content in the record.

`gh issue list --label decision --state open` therefore means *"decisions waiting on
someone"* — check this at the start of every session.

## Labels

| Label | Applies to | Meaning |
|---|---|---|
| `decision` | issue | A decision record |
| `design` / `arch` / `impl` | issue | Decision tier — exactly one, alongside `decision` |
| `task` | issue | A unit of work; assignment is the lock |
| `manual` | issue | Milestone scope requiring a human; **not** agent-claimable, never also `task` |
| `unreviewed` | issue | Decided by an agent without consultation; **awaiting a human's eyes** |
| `superseded` | issue | Decision replaced by a later one |
| `blocked` | issue | Waiting on a decision or external input |
| `suspended` | **PR** | Suspended pending a decision; resume note committed |

`suspended` goes on the pull request, not the issue — it is what the supervisor scan
filters on. The corresponding task issue gets `blocked`.

`unreviewed` exists because a closed issue is invisible. An agent that opens and
immediately closes a decision issue has technically recorded it and practically hidden it:
`--state open` will never show it again, and nobody reads closed issues for fun. The label
is the fix, and clearing it is a human act. See *Deciding without asking*.

---

## When to expose options

Before implementing **any choice where two or more real options exist**, present them
first. This applies at all three tiers, and to choices as granular as which of two
equivalent groupings to use.

Do NOT silently commit to an approach. Present options, state a recommendation, and wait
for direction.

### The two standing options

Two options are on the menu for every decision, before the substantive alternatives,
because they are precisely the ones the framing of a question tends to hide. Number and
letter them distinctly from `A`/`B`/`C` so a recommendation still reads naturally.

**Option 0 — Postpone for team discussion.**
Suspends this task: opens the decision issue, commits a resume note, updates the draft PR,
and stops. Work resumes automatically once the issue is closed.

**Option U — Options by context: don't decide, expose the choice.**
Both outcomes are legitimate for different users in different contexts. Choosing one here
deletes a use case. Instead, surface it as a parameter with a default, and give it a place
in the human review process where a person meets the choice with their own context in hand.

Option U is not decoration. It is the local form of an organizational principle —
*options by context precludes the search for single or blanket solutions* — and the most
common way an agent violates that principle is not by choosing badly, but by asking a
question that presupposes a choice must be made at all.

### The Option U test — mandatory, every fork

**Every option you present must carry a `Serves:` line naming the user and context that
wants it** — or explicitly stating that no constituency could be identified.

```
**Option A — <name>**
<description>
Serves: <who wants this outcome, in what context — or "no constituency identified">
Pros: ...  Cons: ...
```

This is the whole mechanism, and the negative case is the load-bearing half. A rule that
says "consider whether to expose this" gets pattern-matched past within three sessions. A
rule that requires you to *write a sentence naming who wants the losing option, or to state
in writing that nobody does*, forces the search — and makes a lazy answer visible to a
reviewer.

The consequence is a hard rule:

> **If two or more options have a named constituency, Option U must be listed, and it must
> be the recommendation unless you can state why its cost is not worth paying.**

If exactly one option has a constituency, say so plainly — "no constituency identified for
Option A; it appears strictly dominated" — and that sentence justifies deciding without
consultation.

### What Option U costs

It is not free, and treating it as free is its own failure mode.

- It costs a parameter, a default, test coverage on both paths, and a place in the review
  UI where a person actually meets the choice. A parameter with no review surface is not
  Option U; it is a hidden decision with extra steps.
- **Reject it when the other use case is hypothetical.** A parameter nobody sets is worse
  than a decision, because it advertises a flexibility the code does not really support and
  the second path rots untested.
- **Option U relocates the decision; it does not dissolve it.** You still choose the
  default, and the default is where a blanket solution creeps back in. That default is
  usually a genuine `[IMPL]` call: make it, record it, move on.

### Format for presenting options in conversation

```
**[DESIGN|ARCH|IMPL] Decision: <short title>**

**Problem:** <one sentence on what forced this choice>

**Option 0 — Postpone for team discussion**
Suspends this task: opens the decision issue, commits a resume note, updates the
draft PR, and stops. Work resumes automatically once the issue is closed.

**Option U — Expose the choice rather than making it**
<what the parameter is, what the default would be, where a person meets it>
Serves: <both constituencies named below>
Cost: <the parameter, the default, the review surface>

**Option A — <name>**
<description>
Serves: <who wants this, in what context — or "no constituency identified">
Pros: ...  Cons: ...

**Option B — <name>**
<description>
Serves: <who wants this, in what context — or "no constituency identified">
Pros: ...  Cons: ...

**Recommendation:** Option X — <one sentence why>
**Confidence:** high | medium | low — <what would change it>
```

Omit Option U only when the `Serves:` lines show a single constituency. Never omit the
`Serves:` lines.

### Confidence, and the unread-acceptance rule

**Confidence is for triage.** A reader with four decisions in front of them needs to know
which one repays attention. "Low — I cannot name who Option A serves" is an invitation to
read; "high" is permission to skim.

**Assume your recommendation will be accepted without being read.** This is not cynicism
about the reader; it is the realistic steady state of any protocol that asks often enough
to be useful. It implies a hard standard:

> If the recommendation being accepted unread would be a bad outcome, the recommendation is
> wrong — not the reader.

A recommendation you would not defend on its own is a decision you have not finished
making. Finish it, or raise the tier and postpone. Once this holds, clicking through is
safe by construction, which is what makes it affordable to ask more often.

### Batching, and the attention budget

**Do not present a decision at the moment it arises.** Accumulate and present at a natural
checkpoint — the end of an increment, or before starting work that depends on the answer.
Interrupting per fork is what turns consultation into noise, and noise into click-through.

**Soft budget: about three presented decisions per session.** This is a budget, not a
quota — presenting none is fine. But if a fourth arises, that is evidence about the *task*,
not the decisions: the task was underspecified, or it is really several tasks. **Say so
out loud** rather than asking a fourth time.

Batched presentation also improves the decisions themselves. Two forks seen together often
turn out to be one fork, or to have an answer that only makes sense jointly.

### Deciding without asking

Whether you may decide unilaterally depends on the tier, on reversibility, and on whether a
human is in the loop at all.

**Interactive session** — a person is present and answering. Asking is nearly free, so the
threshold to ask is low, but the batching rule still applies:

- `[DESIGN]` — always present. **An agent never closes a DESIGN issue on its own
  judgment**, in any mode. Propose, never ratify.
- `[ARCH]` — present, unless the change is cheap to reverse *and* only one option has a
  named constituency.
- `[IMPL]` — decide it, record it, and mention it in the batch summary. Do not spend a
  presentation slot on an IMPL fork unless the Option U test surfaced a second
  constituency — in which case it is not really an IMPL fork.

**Autonomous session** — spawned agent, supervisor pass, scheduled run; nobody is going to
answer. Waiting is not available, so:

- Decide, implement, and **open the issue with the resolution filled in, close it, and
  apply the `unreviewed` label.**
- Except `[DESIGN]`, which you may not close. Open it, leave it open, and either work
  around it or suspend.
- If the Option U test named two constituencies and you had to pick anyway, say that
  explicitly in the record and label it `unreviewed` regardless of tier.

**The review queue.** `unreviewed` is what makes autonomous decisions consultable after the
fact rather than merely archived:

```bash
# What did agents decide while I was away? Run this at the start of a working week.
gh issue list --label decision --label unreviewed --state all \
  --json number,title,updatedAt \
  --jq '.[] | "#\(.number) \(.title)  (\(.updatedAt[:10]))"'
```

A human clears the label, by removing it. An agent never removes `unreviewed` from its own
decision. If review turns up a wrong call, the correction is a *superseding* issue, not an
edit — the rejected path stays in the record.

### When Option 0 is the right call

Postponement costs a suspend/resume cycle, so it is not the default. Prefer it when
**both** hold:

- the decision is `[DESIGN]` or `[ARCH]` tier, **and**
- it is expensive to reverse — it shapes a data model, a persisted format, a stage
  boundary, or files that don't exist yet.

`[IMPL]` decisions almost never justify suspension. Make the call, record it, move on.

Before suspending, try these in order:

1. **Work around it.** If other work on this branch is unblocked, do that first and
   suspend later, or not at all.
2. **Expose it (Option U).** If the fork is only hard because you are trying to pick for
   everyone, stop picking. This dissolves more blocking decisions than it has any right to.
3. **Stub it.** If the decision can be isolated behind a narrow interface, implement one
   side provisionally, record it as `[IMPL]`, and keep going — but only if switching later
   is genuinely cheap. Say so explicitly in the record.
4. **Suspend.** Correct when the decision blocks all remaining work on the branch.

Suspending suspends the *task*, not the person. After suspending, pick up a different
unblocked issue.

---

## When to open or close a decision issue

**Open an issue** as soon as a decision is identified, whether or not it is settled. An
open decision issue with options and no resolution is a valid and useful artifact — it is
how you hand a question to the other person.

**Close the issue** once the decision is settled. Fill in the resolution fields **before
moving on** to the next task.

If a decision is made mid-implementation without being surfaced first, still record it —
open and immediately close the issue, note in the body that it was made implicitly, and
apply `unreviewed`.

**Issue title:** `[DESIGN|ARCH|IMPL] <Title>`

**Issue body template:**

```markdown
**Status:** Proposed | Decided | Superseded by #N
**Mode:** presented | auto | postponed | option-u
**Outcome:** recommended | listed-alternative | unlisted | option-u | (blank while Proposed)
**Reversibility:** cheap | moderate | expensive

**Problem:**
<What forced this decision — the constraint, conflict, or fork that made it necessary.>

**Options Considered:**

### Option A: <Name>
Serves: <who wants this, in what context — or "no constituency identified">
<Description, including key properties, pros, cons.>

### Option B: <Name>
Serves: <who wants this, in what context — or "no constituency identified">
<Description, including key properties, pros, cons.>

### Option U: Expose the choice
<Included whenever two or more options above have a named constituency. If it was
considered and rejected, keep it here with the reason — that rejection is data.>

**Decision:** <Option chosen and one-sentence summary. Omit while Proposed.>

**Rationale:** <Why this option over the others.>

**Rejected Paths:** <What was explicitly not chosen, and why — this is the most important field.>

**Open Questions:** <What remains uncertain or may require revisiting.>

**Related Decisions:** Motivates: #N / Motivated by: #N / Relates to: #N
```

The four header fields are **structured data**, not prose — they are read by the metric
queries in *The granularity question*. Keep the exact vocabulary; do not editorialise in
them. Everything below them is for humans.

`Mode` is set when the issue is opened. `Outcome` is set when it is closed:

| `Outcome` | Means |
|---|---|
| `recommended` | The reader took the recommendation |
| `listed-alternative` | The reader picked a different option that was on the list |
| `unlisted` | The reader supplied an option the agent had not identified |
| `option-u` | Resolved by exposing the choice rather than making it |

Creating one, with the body in a file to avoid shell-quoting pain:

```bash
gh issue create --label decision --label arch \
  --title "[ARCH] <title>" --body-file <path>
```

Revisiting a settled decision happens in **comments** on the closed issue, not by editing
the body. The comment thread is the amendment history.

**Who closes a decision issue.** If the decision was settled in-session by direction from
the person you are working with, close it yourself. If it was postponed for team
discussion (Option 0), **a human closes it** once the team agrees — closing is the signal
that resolution has happened, so an agent must never close a postponed decision on its own
judgment. `[DESIGN]` issues are always human-closed.

---

# Task Suspension

The ~20% case: a decision needs real team discussion and will take days. Keeping a session
alive is not an option and shouldn't be — the task may be picked up by another person after
the discussion. Suspension makes the *branch* hold the context instead of the session.

## What suspension does

On choosing Option 0, do all five, then stop:

1. **Open the decision issue** with the options filled in, `Status: Proposed`,
   `Mode: postponed`, and a `Blocks: #T` line naming the task issue.
2. **Commit all work in progress** to the branch, even if incomplete or non-building.
   Mark it `wip: <area> — suspended pending #N`. Do not revert to a clean state; reverting
   destroys the information the resume depends on. The branch is a draft, nothing consumes
   it.
3. **Write the resume note** to `.claude/resume/<N>.md`, where `N` is the *decision* issue
   number, and commit it in the same commit as the WIP.
4. **Update the draft PR**: add the `suspended` label, and put the status line at the very
   top of the body (exact format — it is parsed):

   ```
   SUSPENDED: waiting on #N
   ```

5. **Label the task issue** `blocked` and comment on it: `Suspended pending decision #N.`

Then shut down. Do not idle, poll, or hold context.

## Where context lives, and why

| Reader | Reads | Cost |
|---|---|---|
| Supervisor scanning all open PRs | `suspended` label + `SUSPENDED: waiting on #N` line | ~10 tokens/PR |
| Resuming agent, one task only | `.claude/resume/<N>.md` at the branch head | ~500–1500 tokens, once |

This is the same two-tier discipline as decision reading: a cheap index for the scanner,
full context only for the one agent that needs it. A supervisor checking twenty PRs must
never load twenty resume notes.

The resume note is **committed, not a PR comment**, because it describes a specific tree
state. In the tree it travels with the code it documents — `git show` at that commit always
yields a consistent pair. As a comment it silently goes stale the moment anyone pushes.

Naming by decision issue number (`<N>.md`, not `resume.md`) means two suspended branches
can never collide on the same path, even if both are somehow merged.

## Resume note template

```markdown
# Resume: task #T, blocked on decision #N

**Branch:** N-short-slug
**Suspended at:** <commit sha>
**Blocking decision:** #N — <title>

## What is done
<Landed and working, with commit shas. Be specific enough that the resumer does not
re-verify by reading the whole diff.>

## What is in flight
<Partially written code, stubs, anything that does not build. Name the files and say
what state each is in.>

## Conditional continuation
<The most important section. For each option in #N, the concrete next steps if that
option wins. Written now, while the context is hot, so resumption does not re-derive it.>

- **If Option A:** <steps, files to change>
- **If Option B:** <steps, files to change>
- **If Option U:** <where the parameter goes, what the default is, what the review
  surface needs — this branch is easy to forget and expensive to reconstruct>

## Already ruled out
<Approaches explored and rejected during this session, with reasons. Prevents the
resumer from repeating the exploration.>

## Verification state
<What passes now, what is untested, how to check.>
```

**Conditional continuation is what makes this pay off.** Pre-computing each branch of the
decision at suspend time converts a decision outcome directly into an implementation plan.
Without it, resumption means reconstructing the whole problem from a cold start, and
suspension saves nothing.

## Resuming

A supervisor pass finds resumable work:

```bash
# Cheap: which PRs are suspended, and on what?
gh pr list --state open --label suspended --json number,headRefName,body \
  --jq '.[] | "PR #\(.number) \(.headRefName): \(.body | split("\n")[0])"'

# For each blocking decision number found, is it settled?
gh issue view N --json state,body
```

If the decision is **closed**, spawn a fresh agent to resume. That agent reads
`.claude/resume/<N>.md` at the branch head and the closed issue body — nothing else from
the original session is needed or available.

Two guards on resumption:

- **Do not resume on a bare close.** If the closed decision has no filled `**Decision:**`
  field, the outcome is not recorded. Reopen it, comment asking for the resolution, and
  leave the task suspended. Never infer what the team decided from the comment thread.
- **Do not resume down an unlisted path.** If the chosen option is not one of the options
  covered in the resume note's conditional continuation — the team invented Option D —
  ignore the conditional continuation entirely and re-plan from the decision. Say so in
  the PR body. A stale conditional branch is worse than none. Also set
  `Outcome: unlisted` on the decision issue; that case is exactly what the metrics are
  trying to count.

On resuming: remove the `suspended` label and the `SUSPENDED:` line, remove `blocked` from
the task issue, **delete `.claude/resume/<N>.md`**, and commit that deletion with the first
real commit of the resumed work.

## Suspension hygiene

- **Suspended is not stale.** The staleness rule under Issue-as-lock does not apply to an
  issue labelled `blocked` with an open suspended PR. Do not offer to take it over; the
  claim is valid and waiting. If the *decision* has been sitting unanswered for days, that
  is a prompt to discuss the decision, not to seize the task.
- **`.claude/resume/` must be empty before a PR leaves draft.** A resume note in a merge is
  a bug. Check it when marking a PR ready for review.
- **One suspension per branch.** If resumed work hits a second blocking decision, suspend
  again with a new note keyed to the new decision number. Do not accumulate live notes.

---

## Reading decisions without exhausting context

**This is a hard constraint, not a suggestion.** Decision bodies run 400–700 tokens each.
Loading 200 of them is ~110k tokens and will destroy the session. Loading their titles is
~3k.

**Always start with the index. Never fetch bodies speculatively.**

```bash
# Tier 1 — the index. ~15 tokens/issue. Safe to run every session.
gh issue list --label decision --state all --limit 300 \
  --json number,title,state \
  --jq '.[] | "#\(.number) [\(.state)] \(.title)"'
```

The `[TIER]` prefix in the title means the index alone is usually enough to know which
decisions bear on the current task.

```bash
# Tier 2 — narrow by tier or by keyword BEFORE reading anything.
gh issue list --label decision --label design --state all --json number,title
gh issue list --label decision --state all --search "<keywords>"

# Tier 3 — full bodies. Cap at ~10. Name the numbers explicitly; never pipe a whole list.
gh issue view 42 --json title,body,labels
```

Rules:

- Read the index at session start. Do not read bodies until you have a specific reason.
- Fetch at most **10** bodies in one pass. If you think you need more, you are trying to
  reconstruct project history rather than answer a question — narrow the question instead.
- Prefer `--search` over fetching-and-filtering. Filtering server-side costs nothing;
  filtering in context costs everything.
- When citing a decision in a commit, PR, or new issue, cite `#N` — never paste the body.

The same discipline applies to any frozen pre-migration decision archive named in Part 1:
grep it for the specific thing you need; never read it whole.

---

# Task Protocol

There is no tracking file; **issue state is the tracker.**

## Milestones gate what may be worked on

Task issues are assigned to a **GitHub milestone** named `W<wave>-<Stream>`. The streams are
listed in Part 1; the wave grouping lives in the roadmap document named there. The milestone
answers a question the labels cannot: *is this issue approved to be worked on yet?*

**Only issues in an active milestone are available to claim.** Everything else is a backlog —
recorded so the knowledge is not lost, deliberately not actionable.

**The active set is every open milestone in the lowest open wave, minus any on hold.** The
wave is the unit of greenlighting: several streams run in parallel within a wave, and the next
wave stays backlog until the current one closes. All planned milestones exist and are open
from the start, because a milestone must exist before issues can be filed against it, and
filing future work is the whole point.

**A stream can be held out of an active wave.** GitHub gives milestones only open and closed,
where closed means *delivered* — so closing a milestone to defer it would be a lie. Instead,
a held milestone's **description begins with `HOLD:` followed by a reason**. It stays open,
issues can still be filed against it, and it is excluded from the claimable set until the
prefix is removed. The title never changes, so `--milestone "W1-Web"` keeps working and no
saved query breaks.

Hold is a **blocklist, not an allowlist**: a milestone in the lowest open wave is claimable by
default. That fails safe — a newly created milestone is visible and actionable rather than
silently inert, and holding something is always a deliberate act with a written reason.

The consequence worth internalising: **an open milestone does not mean approved work.** A
milestone is claimable only if its wave is the lowest open one *and* it is not on hold.

This is what makes it safe to write down future work in detail. Without milestones, a
well-specified wave-3 issue sitting in the open task list is an invitation to work on
something nobody has agreed to yet; with them, it is a plan.

Rules:

- **Every task issue gets a milestone at creation.** An unmilestoned task issue is a bug —
  it is invisible to the "what is approved" filter and will be either worked on prematurely
  or forgotten.
- **Never move an issue into an active milestone to justify working on it**, and never remove
  a `HOLD:` prefix to unblock yourself. Both are scope decisions, and scope decisions belong
  to the team. If an issue looks misfiled, comment on it and say so.
- **Decision issues are not milestoned.** A decision may be raised at any time regardless of
  which wave is open, and a decision blocking wave-3 work is worth recording the moment it is
  noticed. The `decision` label is the filter for those; the milestone filter is for work.
- **Purely human/manual work gets a milestone but not the `task` label.** Obtaining data,
  securing access, and curating evaluation material are real milestone scope, but they are
  not agent-claimable and must not appear in the claimable queue. Label them `manual`.
- **A wave closes when all its milestones close.** Any issue still open in a closing milestone
  must be explicitly moved to a later wave or closed — never left behind in a closed
  milestone, where it is visible to no filter at all.

`gh` has no `milestone` subcommand, so creating one goes through the API; everything else is
a flag:

```bash
# Create a milestone (one-off, per roadmap milestone)
gh api repos/:owner/:repo/milestones -f title="W1-<Stream>" \
  -f description="<what this stream delivers in this wave>"

# Put a stream on hold (excluded from the active set; stays open, keeps its issues)
gh api -X PATCH repos/:owner/:repo/milestones/N \
  -f description="HOLD: <reason>"

# THE active-set query: open milestones in the lowest open wave, excluding held ones.
# Numeric sort on the wave prefix, so W10 does not sort before W2.
gh api repos/:owner/:repo/milestones --paginate --jq '
  [.[] | select(.state=="open") | select(.title | test("^W[0-9]+-"))]
  | (map(.title | capture("^W(?<w>[0-9]+)-").w | tonumber) | min) as $wave
  | map(select((.title | startswith("W\($wave)-"))
               and ((.description // "") | startswith("HOLD:") | not)))
  | .[].title'

# What is held, and why? Worth checking before asking "can I work on X".
gh api repos/:owner/:repo/milestones --jq \
  '.[] | select(.state=="open") | select((.description // "") | startswith("HOLD:"))
       | "\(.title) — \(.description)"'

# All milestones with issue counts, for context
gh api repos/:owner/:repo/milestones --jq \
  '.[] | "\(.title) [\(.state)] open=\(.open_issues) closed=\(.closed_issues)"'

# Create an issue in a milestone
gh issue create --label task --milestone "W1-<Stream>" --title "..." --body-file <path>

# THE session-start query: what is approved, unclaimed, and ready?
# Run once per milestone returned by the active-set query above.
gh issue list --label task --milestone "W1-<Stream>" --state open \
  --json number,title,assignees,labels

# Backlog for a future wave — read for context, do not claim
gh issue list --milestone "W3-<Stream>" --state open --json number,title

# Close a milestone when it ships
gh api -X PATCH repos/:owner/:repo/milestones/N -f state=closed
```

## Issue-as-lock

An issue is **claimed** if either is true:

- it has an assignee, **or**
- an open PR references it (`Closes #N`).

**To claim, do all three before writing code:**

1. `gh issue edit N --add-assignee @me`
2. Create the branch: `N-short-slug` (issue number first — this makes ownership readable
   in `git branch -r`)
3. Push immediately and open a **draft PR** with `Closes #N` in the body.

The draft PR is what makes the claim visible to someone who is asleep. An unpushed local
branch is not a claim, and neither is a local worktree. **Push the empty branch before doing
the work**, not after.

**Never start work on a claimed issue.** If it's claimed and you believe it's stale
(no commits for several days), comment on the issue asking to take it over — do not
unassign someone unilaterally. **An issue labelled `blocked` with an open `suspended` PR
is not stale** — it is waiting on a decision by design. See Task Suspension.

**Releasing a claim:** unassign, close the draft PR, comment on the issue with what you
learned and why you stopped. Delete the branch only if it has no commits worth keeping.

## Worktrees

Parallel agents on one clone share a checkout, so each concurrent task gets its own git
worktree. Two rules, both learned the hard way:

- **The worktree directory is not the claim; the pushed branch is.** Name the branch
  `N-short-slug` as above. The worktree directory may share that name for legibility, but
  nothing reads it. An agent that creates a worktree and starts work without pushing has
  claimed nothing, and a second agent will take the same issue.
- **Worktree directories must be ignored, never tracked.** A worktree checked out beneath
  the repo root appears to the parent as a gitlink and will show up as a permanently dirty
  entry in `git status`, and can be committed by accident. Add the worktree root to
  `.gitignore`. Note that this cuts against `.claude/resume/`, which **must** be committed:
  ignore the worktree path specifically, not `.claude/`.

```bash
git worktree add <worktree-root>/N-short-slug -b N-short-slug
git -C <worktree-root>/N-short-slug push -u origin N-short-slug   # claim, before any work
git worktree remove <worktree-root>/N-short-slug                  # when the PR merges
git worktree prune
```

The worktree root for this repo is named in Part 1.

## Choosing what to work on

```bash
# Scope the queue to the active milestones (see the active-set query above).
# Issues outside them are not available. Run per active milestone.
gh issue list --label task --milestone "W1-<Stream>" --state open \
  --json number,title,assignees,labels
gh pr list --state open --json number,title,headRefName,isDraft
git fetch --prune && git branch -r
```

Filter out claimed issues, then rank the remainder by **predicted file overlap** with
work that is currently in flight.

**Overlap is a ranking signal, not a gate.** Reason about which paths an issue is likely
to touch and prefer issues whose predicted paths are disjoint from open PRs. But scope
prediction is unreliable, especially early — do not leave the queue empty rather than
accept a possible conflict, and do not refuse to expand scope mid-task because it
crosses a predicted boundary. Take the work, note the overlap in the PR body, and
coordinate in the PR if it materializes.

**Where conflicts actually come from.** Most work creates *new* files, and new files
essentially never conflict. The real conflict surface is the small set of **hub files**
that everything registers into — schema definitions, package exports, registries, config
modules, shared type modules, doc indexes. Those deserve genuine serialisation: if an open
PR touches a hub file, prefer an issue that doesn't, or wait for the merge. Two issues that
each add unrelated modules can proceed in parallel without thought, even if they nominally
"share a directory." The hub files for this repo are marked in the repository map in Part 1.

When opening a task issue, record the predicted paths so this reasoning is cheap for the
next person:

```
**Likely paths:** <dirs>
**Hub files touched:** <paths, or "none">
```

## Cross-repo work

Where Part 1 names a companion repo, the two move together and neither can see the other's
branches. Three rules:

- **A contract change is a decision in the repo that owns the contract**, cited from the
  other by `owner/repo#N`. Do not record the same decision twice; a duplicate that drifts is
  worse than a link.
- **Work that spans both repos is two task issues**, one per repo, cross-linked. A single
  issue cannot be claimed twice, and a PR cannot span repos.
- **The consumer's issue names the producer's issue as a blocker.** If the producing side is
  not yet merged, build against the documented contract and say so in the PR body rather
  than waiting.

## Handoff state

**Handoff lives in the draft PR body.** Not in a file — a committed `HANDOFF.md` conflicts
on every merge, and a gitignored one is invisible to the person who needs it.

Keep this block current at the top of the PR body, updated at the end of each working
session:

```markdown
SUSPENDED: waiting on #N        <- only while suspended; must be the first line

## State
**Done:** <what is landed and working on this branch>
**Next:** <the single next concrete step>
**Blocked on:** <question for the other person, or "nothing">
**Decisions raised:** #N, #N
**Unreviewed:** #N              <- decisions made without consultation on this branch
```

The `SUSPENDED:` line is parsed by the supervisor scan, so keep the format exact and keep
it on line one. Everything below it is for humans.

If you are blocked on a question, also comment on the relevant **issue** and leave it
open — the PR body is where someone looks when they already care about your branch; the
open issue list is where they look when they don't yet.

For handoffs not attached to a branch (a decision needing input, a discovered problem),
comment on the issue. Comments never conflict.

## Starting a session

1. `git fetch --prune`
2. `gh pr list --state open --label suspended` — **is anything resumable?** A suspended
   task whose decision has closed outranks starting new work; the context is already
   written and the team is waiting on it.
3. `gh issue list --label decision --state open` — what needs my input?
4. `gh issue list --label decision --label unreviewed --state all` — what did agents decide
   unsupervised? Surface these to the person you are working with; do not clear the label.
5. `gh pr list --state open` — what is in flight, and what changed overnight?
6. **Which milestones are active?** — the open ones in the lowest open wave, minus any whose
   description starts with `HOLD:`. Everything claimable lives in those; everything else is
   backlog, however open it looks. Use the active-set query above.
7. Read the decision index (Tier 1 above) if touching unfamiliar ground.

---

# Commit Protocol

Commit messages are the primary async review surface — the other person reads them before
they read the diff.

- Commit at each **coherent decision or working increment**, not at end of session.
- Subject: `<area>: <what changed>`, imperative, ≤72 chars.
- Body: **why**, not what. The diff shows what.
- Reference issues: `Refs #N` for progress, `Closes #N` on the PR only.
- Cite decisions by number when a commit implements one: `Implements #42`.
- If a commit embodies a judgment call not yet recorded, open the decision issue in the
  same working session and reference it.

---

# Repo Conventions

- Branches: `N-short-slug`, where `N` is the issue number.
- Never commit directly to `main`. Everything lands via PR, so the other person has a
  reviewable, comment-able record they can read on their own schedule.
- Keep PRs small enough to review in one sitting across a timezone gap.
- `.claude/resume/` holds suspension context and **must be committed** — do not gitignore
  it. It must also be empty on any PR leaving draft.
- The worktree root **must** be gitignored. See *Worktrees*.

---

# The granularity question

This protocol asks a human to weigh in on some decisions and not others, and the line it
draws is a guess. This section says what the guess is, why it is probably wrong, and how the
protocol measures itself — because the same records that coordinate the work are, with four
extra fields, a dataset about when consultation is worth its cost.

## The two failure modes

- **Ask about everything.** High fidelity, and unusable. The cost lands as decision fatigue,
  and fatigue does not show up as complaint — it shows up as *acceptance*. A reader who
  clicks through every recommendation has the same measured intervention rate as a reader who
  agreed with all of them, and the protocol cannot tell the difference.
- **Decide and file.** Fast, and hollow. Decisions get recorded rather than consulted; the
  person nominally in the loop meets each one as a fait accompli, if at all.

Neither is a preference to be split. They are symptoms of gating on the wrong property.

## The hypothesis

> **H1.** The decisions where consultation pays are not the high-tier ones. They are the ones
> that **hard-code an assumption about the end user's context** — a property roughly
> orthogonal to `DESIGN`/`ARCH`/`IMPL`. A default threshold is `[IMPL]` and highly
> consultation-worthy. A module boundary is `[ARCH]` and almost never is.

The corollary is what makes H1 worth testing rather than merely asserting: if it holds, the
fix for a too-fast protocol is not *ask more*. It is *run the Option U test before deciding
whether to ask* — which converts most of those would-be interruptions into design moves
nobody has to adjudicate.

The anecdote H1 comes from is specific and recurring: an agent presents two ways to handle an
ambiguous input, and the answer is neither — both are right for different users, and the
choice belongs to the person using the system. That answer is invisible to a tier-based gate,
which is why the `Serves:` line is mandatory rather than advisory.

## What gets measured

Every metric below comes from the four header fields plus issue state and timestamps. No
extra tooling, no separate log.

| Metric | Computed from | What it decides |
|---|---|---|
| **Intervention rate** | `Outcome != recommended`, over `Mode: presented` | If it approaches zero, presenting was pure cost — either tighten the gate or distrust the number (see fatigue) |
| **Unlisted-option rate** | `Outcome: unlisted` | The value that justifies asking about things the agent believes it understands. Can be high *while* intervention rate is low |
| **Option U rate** | `Outcome: option-u` | Tests H1 directly. If this dominates unlisted outcomes, the gate should be the U-test, not the tier |
| **Regret rate** | `Mode: auto` issues later reopened or `superseded` | The cost of not asking. Free from issue state |
| **Reversibility calibration** | `Reversibility` vs. observed regret | Whether the agent's own cheap/expensive judgment predicts anything |
| **Fatigue proxies** | Batch position, time-to-answer, run-length of consecutive `recommended` | If acceptance rises with position in a batch, the batch is too long. This is the check that keeps intervention rate honest |

Extraction is one query per repo; the schema is identical across every repo on this protocol,
so the corpus is poolable:

```bash
gh issue list --label decision --state all --limit 500 \
  --json number,title,state,labels,body,createdAt,closedAt > decisions.json
```

## Reading the numbers together

No single rate means anything alone, and two of them are actively misleading in isolation:

- **Low intervention + low fatigue proxies** = the gate is too loose; stop presenting so much.
- **Low intervention + high fatigue proxies** = the reader has stopped reading. The
  intervention rate is measuring compliance, not agreement. Shorten batches before touching
  the gate.
- **High regret + low intervention** = the agent is asking about the wrong decisions. It is
  consulting on what it already knows and deciding alone on what it doesn't.
- **High Option U rate** = H1 is surviving. Move effort from the tier gate to the U-test.

## The experiment — described, currently inactive

Observational data confounds: the agent presents the decisions it already suspects are
uncertain, so `presented` and `auto` populations are not comparable, and no amount of data
fixes that.

A causal read on *granularity* needs one randomised arm. The cheap version: for decisions the
agent classifies as **borderline** — the ones the tier gate would resolve either way — decide
by **parity of the issue number** whether to present or to auto-decide-and-flag, then compare
regret rates between arms. The issue exists before the present/auto choice is made, since the
protocol already requires opening it as soon as the decision is identified, so the ordering
works. Parity is free, unbiased with respect to anything about the decision, needs no random
number generator in a specification, and is auditable after the fact by anyone with the issue
list.

Whether the arm is running is a single line in Part 1. **When inactive, follow the ordinary
gate.** Do not randomise on your own initiative, and do not treat this section as permission
to auto-decide something the gate says to present.

Baseline first: observational data is needed to size the arm, and the arm costs interruptions
during precisely the period a new adoption is trying to move fast.

## Why this is worth doing at all

The same records coordinate the work and constitute the dataset, so the marginal cost is four
header fields. The corpus spans several repos, several humans, and unrelated problem domains,
which is more variation than most studies of human-agent interaction get. And under an
open-by-default policy, the protocol and its decision corpus are both publishable — which
makes this a methods contribution about where human judgment belongs in agent-driven work,
rather than local process tuning that dies with the project.

<!-- END SHARED PROTOCOL v1 -->
