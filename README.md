# Orchestration Guide (workflow sequencing)

Portable orchestration for step-based workflows (supervisor + loop runners) with configurable prompt sequencing.

This package can be used as a git submodule across multiple projects. Each project provides its own `orchestration.yaml` configuration file.

## Installation

### As a submodule
```bash
git submodule add <remote-url> scripts/orchestration
```

### Configuration
Create `orchestration.yaml` in your project root:

```yaml
# Workflow sequencing
workflow:
  name: standard        # standard | review_cadence
  review_every_n: 0     # cadence cycles (review_cadence only)

# Prompt paths
prompts_dir: prompts
supervisor_prompt: supervisor.md
main_prompt: main.md
reviewer_prompt: reviewer.md

# State management
state_file: sync/state.json

# Doc/meta auto-commit whitelist (glob patterns)
doc_whitelist:
  - input.md
  - galph_memory.md
  - docs/fix_plan.md
  - plans/**/*.md
  - prompts/**/*.md

# Tracked output globs for auto-commit
tracked_output_globs:
  - tests/fixtures/**/*.npy
  - tests/fixtures/**/*.npz

# Key file paths
findings_file: docs/findings.md
input_file: input.md

# Directories
logs_dir: logs
tmp_dir: tmp

# Router (optional)
router:
  enabled: false
  mode: router_default  # router_default | router_first | router_only
  prompt: router.md
  review_every_n: 0
  allowlist:
    - supervisor.md
    - main.md
    - reviewer.md

# Agent dispatch (optional)
agent:
  default: auto  # auto | claude | codex
  roles:
    supervisor: claude
    loop: codex
  prompts:
    supervisor.md: codex
    main.md: claude
```

The config is loaded by searching upward from CWD for `orchestration.yaml`. If not found, sensible defaults are used.

### Spec Bootstrap (optional)

For bootstrapping specs from an existing implementation:

```yaml
spec_bootstrap:
  templates_dir: ~/Documents/project-templates
  specs:
    dir: specs  # Canonical location; templates discovered from templates_dir/specs (fallback: templates_dir/docs/spec-shards)
  implementation:
    dirs:
      - src/
    exclude:
      - "**/__pycache__/**"
      - "**/tests/**"
  scoring:
    coverage: 80
    accuracy: 85
    consistency: 90
  state_file: sync/spec_bootstrap_state.json
  prompts:
    reviewer: spec_reviewer.md
    writer: spec_writer.md
```

Note: If the `specs.dir` key is omitted, it defaults to `specs/`. Spec shard templates are discovered from `templates_dir/specs/*.md` with a fallback to `templates_dir/docs/spec-shards/*.md` for legacy template layouts. See `docs/index.md` for the full documentation map.

## Overview
- Two runners:
  - `supervisor.sh` → `scripts/orchestration/supervisor.py` (even step_index)
  - `loop.sh` → `scripts/orchestration/loop.py` (odd step_index)
- Combined entrypoint:
  - `orchestrator.sh` → `scripts/orchestration/orchestrator.py` (runs both steps sequentially)
- Modes:
  - Async: local, back‑to‑back steps.
  - Sync via Git: strict step handoff using `sync/state.json` committed and pushed between machines.
- Wrappers call Python by default; set `ORCHESTRATION_PYTHON=0` to force legacy bash logic.

## State File
- Path: `sync/state.json` (tracked and pushed so both machines see updates)
- Fields:
- `workflow_name` (string; default: `standard`)
- `step_index` (int, 0‑based)
- `iteration` (int; legacy alias for `step_index + 1`)
- `expected_step` (string; selected prompt for the current step)
- `status` ("idle" | "running" | "waiting-next" | "complete" | "failed")
- `last_update`, `lease_expires_at` (ISO8601)
- `galph_commit`, `ralph_commit` (short SHAs)
- `last_prompt` (string; set by router-enabled runs only)

## Branch Safety (important)
- Always operate on the intended branch; pass `--branch <name>` to both runners.
- The orchestrators will abort if the current branch is not the specified one.
- Pushes use explicit refspecs: `git push origin HEAD:<branch>` to avoid cross‑branch mistakes.

## Defaults
- Iterations: 20 (`--sync-loops N` to change)
- Poll interval: 5s (`--poll-interval S`)
- No max wait by default (`--max-wait-sec S` to enable)
- Workflow: `standard` (supervisor → main). Set `workflow.name: review_cadence` and `workflow.review_every_n` to insert review cycles.
- Per‑iteration logs under `logs/` (see Logging).

## Router (optional)

Deterministic routing selects the per-step prompt using `sync/state.json` plus optional review cadence.

- Deterministic routing uses `workflow_name` + `step_index` to select a prompt.
- Review cadence: in the `review_cadence` workflow, when `review_every_n > 0` and a cadence cycle hits, both steps in that cycle run `reviewer.md`.
  - Example (`review_every_n=2`): supervisor, main, reviewer, reviewer, supervisor, main, ...
- Allowlist enforcement: selected prompts must be in the allowlist and exist on disk.
- Optional router prompt override: if configured, a router prompt runs after deterministic selection and may override it.
  - Router output must be a single, non-empty line naming a prompt file.
  - Invalid output (empty, missing, or not allowlisted) aborts the run with a descriptive error.
- Router modes:
  - `router_default`: deterministic selection first; router prompt (if configured) may override.
  - `router_first`: router prompt runs first when configured; otherwise deterministic selection is used.
  - `router_only`: router prompt required; deterministic selection is never used.
- State annotation: when router is enabled, the final selected prompt is stored in `sync/state.json` as `last_prompt` only.
- No actor gating: router overrides apply to every step (combined/sync), not just supervisor/loop.

Implementation lives in `scripts/orchestration/router.py` with a thin wrapper `scripts/orchestration/router.sh`.

## Agent Dispatch (optional)

You can route different CLIs per role or per prompt. Role keys are runner labels (`supervisor`/`loop`) and do not influence prompt selection. Legacy `galph`/`ralph` keys are accepted as aliases. Resolution precedence:

1. CLI prompt map (`--agent-prompt`)
2. CLI role map (`--agent-role`)
3. YAML prompt map (`agent.prompts`)
4. YAML role map (`agent.roles`)
5. Default (`--agent` or `agent.default`)

Prompt keys are normalized to `.md` and matched relative to `prompts_dir` (e.g., `supervisor.md`, `subdir/debug.md`).

CLI usage examples:
```bash
./orchestrator.sh --mode combined --agent codex \
  --agent-role supervisor=claude,loop=codex \
  --agent-prompt reviewer.md=claude
```

Environment variables:
- Combined: `ORCHESTRATOR_AGENT_ROLE`, `ORCHESTRATOR_AGENT_PROMPT`
- Supervisor: `SUPERVISOR_AGENT_ROLE`, `SUPERVISOR_AGENT_PROMPT`
- Loop: `LOOP_AGENT_ROLE`, `LOOP_AGENT_PROMPT`

### Doc/meta auto‑commit whitelist
- The supervisor auto‑stages/commits a limited set of doc/meta paths at end of turn to keep the tree clean.
- Default whitelist includes: `input.md`, `galph_memory.md`, `docs/fix_plan.md`, `plans/**/*.md`, `prompts/**/*.md`, and core Git meta files: `.gitignore`, `.gitmodules`, `.gitattributes`.
- Rationale: allow intentional repo‑hygiene edits made by the supervisor without tripping the post‑run guard. Override via `--autocommit-whitelist` if needed.

## Sync via Git (two machines)
1) Preconditions:
   - Both machines share the same remote and branch (e.g., `feature/spec-based-2`).
   - Ensure `sync/state.json` exists; set `workflow_name` and `step_index` (even = supervisor starts).
2) Start supervisor (even step index):
   ```bash
   ORCHESTRATION_BRANCH=feature/spec-based-2    ./supervisor.sh --sync-via-git --branch feature/spec-based-2      --sync-loops 20 --logdir logs --verbose --heartbeat-secs 10
   ```
3) Start loop (odd step index):
   ```bash
   ORCHESTRATION_BRANCH=feature/spec-based-2    ./loop.sh --sync-via-git --branch feature/spec-based-2      --sync-loops 20 --logdir logs
   ```
- Optional wrapper (role-gated orchestrator):
  ```bash
  ./orchestrator.sh --mode role --role galph --sync-via-git --branch feature/spec-based-2 --sync-loops 20 --logdir logs
  ./orchestrator.sh --mode role --role ralph --sync-via-git --branch feature/spec-based-2 --sync-loops 20 --logdir logs
  ```
- Handshake:
  - Supervisor writes: `status=waiting-next`, increments `step_index` on success.
  - Loop writes: `status=complete`, increments `step_index` on success.
  - Supervisor advances when it observes an even `step_index` that has advanced.

## Async (single machine)
- Run without `--sync-via-git` to execute N iterations locally (still writes per‑iteration logs):
  ```bash
  ./supervisor.sh --sync-loops 5 --logdir logs
  ./loop.sh --sync-loops 5 --logdir logs
  ```

## Combined (single machine)

Run both actors sequentially in a single process:

```bash
./orchestrator.sh --mode combined --sync-loops 5 --logdir logs
```

Router notes for combined mode:
- Review cadence is driven by the workflow (review cycles replace both steps when enabled).
- Router overrides (router prompt output) are applied to every step.

### Combined auto-commit (local-only)
- Combined mode can auto-commit doc/meta, reports, and tracked outputs using supervisor defaults.
- Dirty non-whitelist paths are logged as warnings only (no hard failure).
- Auto-commit messages include the role prefix (`SUPERVISOR AUTO` or `RALPH AUTO`), the prompt name, and the iteration tag (for example, `prompt=reviewer.md` and `iter=00017`).
- Use `--commit-dry-run` to log what would be committed without staging.
- Use `--no-git` to skip all git operations in combined mode.

## Logging
- Descriptive per‑iteration logs:
  - Step logs: `logs/<branch>/steps/iter-00017_step-0_YYYYMMDD_HHMMSS.log`
  - Next step: `logs/<branch>/steps/iter-00017_step-1_YYYYMMDD_HHMMSS.log`
- Configure base directory with `--logdir PATH` (default `logs/`).
- If you generate markdown summaries, keep them next to the raw logs and follow `docs/logging/log_summary_conventions.md` as needed.
- Supervisor console options:
  - `--verbose`: print state changes to console and log
  - `--heartbeat-secs N`: periodic heartbeat lines while polling
- `logs/` is ignored by Git.

### Viewing recent interleaved logs

Note: `tail_interleave_logs` currently targets the legacy galph/ralph log layout; step-based logs will need a follow-up update.

Use the helper script to interleave the last N galph/ralph logs (or markdown summaries) for a branch prefix. Entries are matched on iteration number and wrapped in an XML-like tag with CDATA. The tool annotates each log with the post-state commit that stamped the handoff and can optionally snapshot selected directories from that commit:

```bash
python -m scripts.orchestration.tail_interleave_logs feature-spec-based-2 -n 3
# Summaries instead of raw logs:
python -m scripts.orchestration.tail_interleave_logs feature-spec-based-2 -n 3 --source summaries
```

Output structure:

```xml
<logs prefix="feature-spec-based-2" count="3" source="logs">
  <log role="galph" iter="141" path="logs/feature-spec-based-2/galph/iter-00141_....log" source="log" format="text" commit="abc1234" commit_subject="[SYNC i=141] actor=galph → next=ralph status=ok ...">
    <![CDATA[
    ...
    ]]>
    <ls path="docs" commit="abc1234">
      <![CDATA[
      docs/architecture/pytorch_design.md
      ...
      ]]>
    </ls>
  </log>
  <log role="ralph" iter="141" path="logs/feature-spec-based-2/ralph/iter-00141_....log" source="log" format="text" commit="def5678" commit_subject="[SYNC i=142] actor=ralph → next=galph status=ok ...">
    <![CDATA[
    ...
    ]]>
    <!-- Optional ls-tree snapshots repeat for each requested root -->
  </log>
  ...
</logs>
```

Flags of note:

- `-n/--count` tail length (0 = all matching iterations)
- `--min-iter/--max-iter` numeric bounds on iteration selection
- `--no-ls` disables the commit `ls-tree` snapshots
- `--ls-paths docs,plans,reports` overrides which repository roots are listed when `ls` output is enabled
- `--source {logs,summaries}` switches between raw log files and markdown summaries
- `--roles galph,ralph` narrows the interleaved output to a subset of actors (order preserved)

### Manual state handoff (without running a loop)

Use the stamper to advance `sync/state.json` step index and publish without executing a supervisor/loop body:

```bash
# Supervisor stamps success (advances to next step)
python -m scripts.orchestration.stamp_handoff galph ok --branch feature/spec-based-2

# Supervisor marks failure (no handoff)
python -m scripts.orchestration.stamp_handoff galph fail --branch feature/spec-based-2

# Loop stamps success (advances to next step)
python -m scripts.orchestration.stamp_handoff ralph ok --branch feature/spec-based-2

# Loop marks failure (no increment)
python -m scripts.orchestration.stamp_handoff ralph fail --branch feature/spec-based-2
```

Flags:
- `--no-pull` to skip pre-stamp pull; `--no-push` to skip push (local-only)
- `--allow-dirty` to bypass dirty-tree guard (not recommended)

Notes:
- Messages and step semantics match the orchestrators: supervisor stamps `waiting-next` and increments `step_index` on success; loop stamps `complete` and increments `step_index` on success.
- The tool updates `last_update`, `lease_expires_at`, and `galph_commit`/`ralph_commit` using the current HEAD.

## Flag Reference
- Supervisor
  - `--sync-via-git` · `--sync-loops N` · `--poll-interval S` · `--max-wait-sec S`
  - `--branch NAME` (abort if not on this branch)
  - `--logdir PATH` (per‑iteration logs)
  - `--workflow NAME` · `--workflow-review-every-n N` (workflow sequencing + review cadence)
  - `--verbose` · `--heartbeat-secs N`
  - `--auto-commit-docs` / `--no-auto-commit-docs` (default: on)
    - When enabled, supervisor will auto‑stage+commit changes limited to a doc/meta whitelist after a successful run and before handing off:
      - Whitelist (globs): `input.md`, `galph_memory.md`, `docs/fix_plan.md`, `plans/**/*.md`, `prompts/**/*.md`
      - Files must be ≤ `--max-autocommit-bytes` (default 1,048,576 bytes)
      - Any dirty tracked changes outside the whitelist cause a clear error and the handoff is aborted (no state flip)
    - Configure whitelist via `--autocommit-whitelist a,b,c` and size via `--max-autocommit-bytes N`
  - `--tolerate-doc-dirty` to log non-whitelisted dirty paths and continue the handoff (doc/meta auto-commit still runs)
  - Reports auto-commit (publishes supervisor evidence by file type)
    - `--auto-commit-reports` / `--no-auto-commit-reports` (default: on)
    - `--report-extensions ".png,.jpeg,.npy,.txt,.md,.json,.log,.py,.c,.h,.sh"` — allowed file types (logs + source files/scripts)
    - `--report-path-globs "glob1,glob2"` — optional glob allowlist (default allows any path); logs/`tmp/` are always skipped
    - `--max-report-file-bytes N` (default 5 MiB) · `--max-report-total-bytes N` (default 20 MiB)
    - `--force-add-reports` (default: on) — force-add files even if ignored by .gitignore
    - Notes: stamp-first handoff ensures reports + state publish together; adjust caps/extension list / path globs as needed for your workflow.
  - Tracked outputs auto-commit (publishes modified tracked artifacts like fixtures)
    - `--auto-commit-tracked-outputs` / `--no-auto-commit-tracked-outputs` (default: on)
    - `--tracked-output-globs "tests/fixtures/**/*.npy,tests/fixtures/**/*.npz,tests/fixtures/**/*.json,tests/fixtures/**/*.pkl"` — path allowlist (glob); only tracked modifications are considered
    - `--tracked-output-extensions ".npy,.npz,.json,.pkl"` — allowed extensions
    - `--max-tracked-output-file-bytes N` (default 32 MiB) · `--max-tracked-output-total-bytes N` (default 100 MiB)
    - Notes: runs before doc/meta hygiene; keeps repo clean when fixture‑like binaries are legitimately regenerated during a supervisor loop. Files exceeding caps remain dirty and will trigger the whitelist guard (handoff abort).
  - `--prepull-auto-commit-docs` / `--no-prepull-auto-commit-docs` (default: on)
    - If the initial git pull fails (e.g., due to local modified files), supervisor now follows a three-step recovery:
      1) Submodule scrub: `git submodule sync --recursive` then `git submodule update --init --recursive --checkout --force` (with manual gitlink align fallback)  
      2) Tracked outputs auto-commit: stage+commit modified fixture-like files within limits (default globs `tests/fixtures/**/*.npy,*.npz,*.json,*.pkl`)  
      3) Doc/meta whitelist auto-commit: stage+commit changes to `input.md`, `galph_memory.md`, `docs/fix_plan.md`, `plans/**/*.md`, `prompts/**/*.md` within size caps
    - The pull is retried after each step; if dirty paths remain outside these guards, the supervisor exits with a clear error.
- Loop
  - `--sync-via-git` · `--sync-loops N` · `--poll-interval S` · `--max-wait-sec S`
  - `--branch NAME` · `--logdir PATH`
  - `--workflow NAME` · `--workflow-review-every-n N` (workflow sequencing + review cadence)
  - `--allow-dirty` (default: off) to continue when git pull fails (not recommended)
  - Reports auto-commit (publishes loop evidence by file type)
    - `--auto-commit-reports` / `--no-auto-commit-reports` (default: on)
    - `--report-extensions ".png,.jpeg,.npy,.log,.txt,.md,.json,.py,.c,.h,.sh"` — allowed file types (including code diffs/scripts)
    - `--report-path-globs "glob1,glob2"` — optional glob allowlist (default allows any path); logs/`tmp/` are always skipped
    - `--max-report-file-bytes N` (default 5 MiB) · `--max-report-total-bytes N` (default 20 MiB)
    - `--force-add-reports` (default: on) — force-add files even if ignored by .gitignore
    - Notes: stamp-first handoff ensures reports + state publish together; adjust caps/extension list / path globs as needed for your workflow.

- Orchestrator (combined mode)
  - `--no-git` · `--commit-dry-run`
  - `--workflow NAME` · `--workflow-review-every-n N` (workflow sequencing + review cadence)
  - `--auto-commit-docs` / `--no-auto-commit-docs` · `--autocommit-whitelist` · `--max-autocommit-bytes`
  - `--auto-commit-reports` / `--no-auto-commit-reports` · `--report-extensions` · `--report-path-globs`
  - `--auto-commit-tracked-outputs` / `--no-auto-commit-tracked-outputs` · `--tracked-output-globs` · `--tracked-output-extensions`
  - Notes: local-only auto-commit uses supervisor defaults; best-effort warnings on non-whitelist dirt.

## Troubleshooting
- No live console output: the runner uses a pseudo‑TTY by default to encourage streaming from agent CLIs. If you need the old pipe behavior, set `ORCHESTRATION_USE_PTY=0`.
- Pull failures: both orchestrators now fail fast on git pull errors (including untracked‑file or local‑modification collisions). Read the console/log message, resolve locally (commit/stash/move), and rerun.
- Submodule pointer drift: if `.claude/` or other gitlinks appear dirty, the supervisor auto-scrubs submodules (sync + update with `--checkout --force`) before retries. This is idempotent and does not commit pointer bumps; it aligns worktrees to the recorded superproject commits.
- Push rejected / rebase in progress: orchestrators auto‑abort in‑progress rebase before pulling. If conflicts arise, fix them locally, commit, and rerun.
- Branch mismatch: checkout the correct branch or adjust `--branch`.
- Missing prompt: ensure `prompts/<name>.md` exists (default is `main`).

## Notes
- `loop.sh`, `supervisor.sh`, and `input.md` are treated as protected entrypoints elsewhere in the project. Keep wrappers; they manage env and call Python modules by default.
