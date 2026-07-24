# LyreTeXt
An agentic translation system for books into PreTeXt

## Operator CLI (manual UX testing)

The CLI is designed to exercise the orchestration backend directly for manual UX testing.

### What the CLI gives you

- Start a run from a source project directory
- Pause at stage gates using native LangGraph interrupts
- Resume an interrupted run using native `Command(resume=...)` semantics
- Inspect run status and manifest quickly

## Global flags

All commands accept these top-level options:

```powershell
python -m lyretext.cli [FLAGS] <command> [command-args]
```

- `--config <file>`: Path to YAML/JSON config (default: config.yml)
- `--source <dir>`: Project source directory (default: demo/example_rmd_project/source)
- `--temp <dir>`: Temporary working directory (default: demo/temp_output)
- `--output <dir>`: PreTeXt output directory (default: demo/example_output)
- `--checkpointer {memory|sqlite}`: State persistence backend (default: memory)
- `--db-path <file>`: SQLite checkpoint database file (default: lyretext_runs.db, only with --checkpointer sqlite)

## Subcommands

### start — Begin a new run

Invokes the orchestration graph from the source project.

```powershell
python -m lyretext.cli --source "demo\example_rmd_project\source" start
```

**Response:**
```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "stage_id": "read",
  "status": "paused_at_gate",
  "interrupted": true,
  "pending_interrupts": [
    {
      "interrupt_id": "0",
      "type": "validation_gate_blocked",
      "chapter_id": null,
      "value": {...}
    }
  ],
  "manifest_count": 5,
  "chapter_status": {...},
  "checkpoint_id": "abc123",
  "checkpoint_ns": "default"
}
```

### status — Check run state

Get current status without resuming.

```powershell
python -m lyretext.cli status <run_id>
```

Prints the same `RunResponse` structure as `start`.

### manifest — List chapters

Show the manifest (chapter list) for a run.

```powershell
python -m lyretext.cli manifest <run_id>

# JSON format
python -m lyretext.cli manifest <run_id> --raw
```

Output (compact):
```
chapter-1-introduction
chapter-2-central-tendency
chapter-3-measuring-spread
...
```

### resume — Resume an interrupted run

Resume at the current pause point with a decision.

#### At read gate (validation_gate_blocked interrupt)

**Approve and continue:**
```powershell
python -m lyretext.cli resume <run_id> --action approve_continue
```

**Edit manifest (subset of chapters):**
```powershell
python -m lyretext.cli resume <run_id> --action select_chapters --payload '{"chapter_ids": ["chapter-1", "chapter-3"]}'
```

**Skip specific chapters:**
```powershell
python -m lyretext.cli resume <run_id> --action skip_chapters --payload '{"chapter_ids": ["chapter-2"]}'
```

**Replace entire manifest:**
```powershell
python -m lyretext.cli resume <run_id> --action edit_manifest --payload '{"manifest": [...]}'
```

**Trigger recompilation:**
```powershell
python -m lyretext.cli resume <run_id> --action recompile_chapters --payload '{"chapter_ids": ["__all__"]}'
```

**Abort run:**
```powershell
python -m lyretext.cli resume <run_id> --action abort_run
```

#### At chapter gates (chapter_review interrupts)

**Approve all pending chapters:**
```powershell
python -m lyretext.cli resume <run_id> --action approve
```

**Per-chapter decisions:**
```powershell
python -m lyretext.cli resume <run_id> --action approve --chapter-decisions '{"chapter-1": "approve", "chapter-2": "retry", "chapter-3": "skip"}'
```

Available actions per chapter:
- `approve`: Accept chapter output, move to next
- `retry`: Re-run translation (max 2 retries before escalation)
- `skip`: Skip this chapter
- `approve_after_escalation`: Force approval after 2 retries (escalation override)

### stream — Watch live progress (Phase D)

Emit line-delimited JSON events for a run (stub in MVP; full streaming pending).

```powershell
python -m lyretext.cli stream <run_id>
```

### list — Past runs

List all runs stored in checkpoint database (SQLite only).

```powershell
python -m lyretext.cli list
```

Returns JSON list of thread metadata. Returns error if using memory backend.

### inspect — Full state snapshot

Dump the complete state dict for a run.

```powershell
python -m lyretext.cli inspect <run_id>
```

Returns the full `TranslationState` as JSON for debugging.

## RunResponse structure

All commands (start, resume, status, stream) emit a standard response:

```json
{
  "run_id": "uuid",
  "stage_id": "read|translate|unknown",
  "status": "running|paused_at_gate|completed|failed",
  "interrupted": true/false,
  "pending_interrupts": [
    {
      "interrupt_id": "0",
      "type": "validation_gate_blocked|chapter_review",
      "chapter_id": "chapter-1|null",
      "value": {...context...}
    }
  ],
  "manifest_count": 5,
  "chapter_status": {"chapter-1": "pending|running|succeeded|failed|skipped"},
  "checkpoint_id": "abc123",
  "checkpoint_ns": "default"
}
```

## Checkpoint backends

### Memory (default, ephemeral)

State is held in memory for the duration of the process. Runs are lost on exit.

```powershell
python -m lyretext.cli start
```

### SQLite (persistent)

State is saved to a SQLite database file, surviving process restarts and CLI invocations.

```powershell
python -m lyretext.cli --checkpointer sqlite --db-path runs.db start

# Later, even in a new terminal:
python -m lyretext.cli --checkpointer sqlite --db-path runs.db status <run_id>
```

## Example workflow

1. **Start a run:**
```powershell
$run = (python -m lyretext.cli start | ConvertFrom-Json)
$run_id = $run.run_id
Write-Host "Run: $run_id"
```

2. **Check status:**
```powershell
python -m lyretext.cli status $run_id
```

3. **Review manifest:**
```powershell
python -m lyretext.cli manifest $run_id
```

4. **Approve read gate:**
```powershell
python -m lyretext.cli resume $run_id --action approve_continue
```

5. **Chapter-level decisions (if paused at chapter gate):**
```powershell
python -m lyretext.cli resume $run_id --action approve --chapter-decisions '{"chapter-1": "approve", "chapter-2": "retry"}'
```

6. **Persist across terminal sessions (SQLite):**
```powershell
python -m lyretext.cli --checkpointer sqlite --db-path runs.db start
# ... close terminal, reopen in same directory ...
python -m lyretext.cli --checkpointer sqlite --db-path runs.db list
```
