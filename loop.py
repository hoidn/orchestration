from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from pathlib import Path, PurePath
from .agent_dispatch import (
    AgentConfig,
    canonical_prompt_key,
    normalize_prompt_map,
    normalize_role_key,
    normalize_role_map,
    parse_agent_map,
    prompt_key_from_path,
    select_agent_cmd,
    resolve_cmd,
)
from .state import OrchestrationState
from .git_bus import safe_pull, add, commit, push_to, short_head, has_unpushed_commits, assert_on_branch, current_branch, push_with_rebase
from .autocommit import autocommit_reports
from .config import load_config
from .runner import RouterContext, log_file, run_router_prompt, select_prompt, tee_run
from .router import resolve_prompt_path


def main() -> int:
    # Load orchestration config (searches upward for orchestration.yaml)
    cfg = load_config(warn_missing=False)

    ap = argparse.ArgumentParser(description="Engineer (ralph) orchestrator")
    ap.add_argument("--sync-via-git", action="store_true", help="Enable cross-machine synchronous mode via Git state")
    ap.add_argument("--no-git", action="store_true", help="Disable all git operations (for local-only runs like spec bootstrap)")
    ap.add_argument("--sync-loops", type=int, default=int(os.getenv("SYNC_LOOPS", 20)))
    ap.add_argument("--poll-interval", type=int, default=int(os.getenv("POLL_INTERVAL", 5)))
    ap.add_argument("--max-wait-sec", type=int, default=int(os.getenv("MAX_WAIT_SEC", 0)))
    ap.add_argument("--state-file", type=Path, default=Path(os.getenv("STATE_FILE", str(cfg.state_file))))
    ap.add_argument("--claude-cmd", type=str, default=os.getenv("CLAUDE_CMD", ""))
    ap.add_argument("--codex-cmd", type=str, default=os.getenv("CODEX_CMD", "codex"))
    ap.add_argument("--agent", type=str, choices=["auto", "claude", "codex"], default=os.getenv("LOOP_AGENT", cfg.agent_default),
                    help="Model CLI used for engineer loops (auto: prefer Claude, fallback Codex).")
    ap.add_argument("--agent-role", type=str, default=os.getenv("LOOP_AGENT_ROLE", ""))
    ap.add_argument("--agent-prompt", type=str, default=os.getenv("LOOP_AGENT_PROMPT", ""))
    ap.add_argument("--prompt", type=str, default=os.getenv("LOOP_PROMPT", "main"),
                    help="Prompt file name (without path), e.g. 'spec_writer' or 'main' (default: main)")
    ap.add_argument("--use-router", dest="use_router", action="store_true",
                    help="Enable router-based prompt selection (overrides --prompt).")
    ap.add_argument("--no-router", dest="use_router", action="store_false",
                    help="Disable router prompt selection even if config enables it.")
    ap.set_defaults(use_router=cfg.router_enabled)
    ap.add_argument("--workflow", type=str, default=os.getenv("ORCHESTRATION_WORKFLOW", cfg.workflow_name),
                    help="Workflow name for prompt sequencing (default: config).")
    workflow_review_default = os.getenv("ORCHESTRATION_WORKFLOW_REVIEW_EVERY_N")
    if workflow_review_default is None:
        workflow_review_default = os.getenv("ROUTER_REVIEW_EVERY_N")
    if workflow_review_default is None:
        workflow_review_default = str(cfg.workflow_review_every_n)
    ap.add_argument("--workflow-review-every-n", type=int,
                    default=int(workflow_review_default),
                    help="Review cadence in cycles (workflow review_cadence).")
    ap.add_argument("--router-prompt", type=str, default=os.getenv("ROUTER_PROMPT", cfg.router_prompt or ""),
                    help="Router prompt file name (relative to prompts_dir) for override decisions.")
    ap.add_argument("--router-review-every-n", dest="workflow_review_every_n", type=int,
                    default=argparse.SUPPRESS,
                    help="Deprecated alias for --workflow-review-every-n.")
    ap.add_argument("--router-allowlist", type=str,
                    default=os.getenv("ROUTER_ALLOWLIST", ",".join(cfg.router_allowlist)))
    ap.add_argument("--router-prompt-supervisor", type=str,
                    default=os.getenv("ROUTER_PROMPT_SUPERVISOR", cfg.supervisor_prompt))
    ap.add_argument("--router-prompt-main", type=str,
                    default=os.getenv("ROUTER_PROMPT_MAIN", cfg.main_prompt))
    ap.add_argument("--router-prompt-reviewer", type=str,
                    default=os.getenv("ROUTER_PROMPT_REVIEWER", cfg.reviewer_prompt))
    ap.add_argument("--router-mode", type=str,
                    default=os.getenv("ROUTER_MODE", cfg.router_mode),
                    help="Router mode: router_default, router_first, router_only.")
    ap.add_argument("--branch", type=str, default=os.getenv("ORCHESTRATION_BRANCH", ""))
    ap.add_argument("--logdir", type=Path, default=Path("logs"), help="Base directory for per-iteration logs (default: logs/)")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="Allow continuing when git pull fails (use with care)")
    # Reports auto-commit (engineer evidence publishing)
    ap.add_argument("--auto-commit-reports", dest="auto_commit_reports", action="store_true",
                    help="Auto-stage+commit report artifacts by file extension after run (default: on)")
    ap.add_argument("--no-auto-commit-reports", dest="auto_commit_reports", action="store_false",
                    help="Disable auto commit of report artifacts")
    ap.set_defaults(auto_commit_reports=True)
    ap.add_argument("--report-extensions", type=str,
                    default=os.getenv("REPORT_EXTENSIONS", ".png,.jpeg,.npy,.log,.txt,.md,.json,.py,.c,.h,.sh"),
                    help="Comma-separated list of allowed report file extensions (lowercase, with dots)")
    ap.add_argument("--max-report-file-bytes", type=int, default=int(os.getenv("MAX_REPORT_FILE_BYTES", "5242880")),
                    help="Maximum per-file size (bytes) eligible for reports auto-commit (default 5 MiB)")
    ap.add_argument("--max-report-total-bytes", type=int, default=int(os.getenv("MAX_REPORT_TOTAL_BYTES", "20971520")),
                    help="Maximum total size (bytes) staged per iteration for reports (default 20 MiB)")
    ap.add_argument("--force-add-reports", dest="force_add_reports", action="store_true",
                    help="Force-add report files even if ignored (.gitignore) (default: on)")
    ap.add_argument("--no-force-add-reports", dest="force_add_reports", action="store_false",
                    help="Do not force-add ignored report files")
    ap.set_defaults(force_add_reports=True)
    ap.add_argument("--report-path-globs", type=str,
                    default=os.getenv("REPORT_PATH_GLOBS", ""),
                    help="Comma-separated glob allowlist for report auto-commit paths (default: none)")

    args, unknown = ap.parse_known_args()

    try:
        cli_role_map = parse_agent_map(args.agent_role, normalize_role_key)
        cli_prompt_map = parse_agent_map(
            args.agent_prompt,
            lambda key: canonical_prompt_key(key, cfg.prompts_dir),
        )
    except ValueError as exc:
        print(f"[loop] ERROR: {exc}")
        return 2

    agent_cfg = AgentConfig(
        default_agent=args.agent,
        role_map=normalize_role_map(cfg.agent_roles),
        prompt_map=normalize_prompt_map(cfg.agent_prompts, cfg.prompts_dir),
        prompts_dir=cfg.prompts_dir,
    )

    log_path = log_file("claudelog")
    report_path_globs = tuple(p.strip() for p in args.report_path_globs.split(',') if p.strip())
    logdir_prefix_parts = tuple(part for part in PurePath(args.logdir).parts if part not in {"", "."})
    skip_config_path = Path(os.getenv("REPORT_SKIP_CONFIG", ".reportsignore"))
    skip_prefix_specs: tuple[tuple[str, ...], ...] = tuple()
    if skip_config_path.exists():
        specs: list[tuple[str, ...]] = []
        for raw_line in skip_config_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.partition("#")[0].strip()
            if not line:
                continue
            parts = tuple(part for part in PurePath(line).parts if part not in {"", "."})
            if parts:
                specs.append(parts)
        skip_prefix_specs = tuple(specs)

    def _within(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
        return bool(prefix) and parts[:len(prefix)] == prefix

    def _skip_reports(path: str) -> bool:
        parts = PurePath(path).parts
        if _within(parts, logdir_prefix_parts):
            return True
        if parts and parts[0] == "tmp":
            return True
        for spec in skip_prefix_specs:
            if spec and parts[:len(spec)] == spec:
                return True
        return False

    def _is_loop_turn(state: OrchestrationState) -> bool:
        return state.step_index % 2 == 1

    def logp(msg: str) -> None:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

    def _router_allowlist() -> list[str] | None:
        allowlist = [item.strip() for item in args.router_allowlist.split(",") if item.strip()]
        return allowlist or None

    def _router_state(iteration_override: int | None = None) -> OrchestrationState:
        if args.state_file.exists():
            state = OrchestrationState.read(str(args.state_file))
        else:
            state = OrchestrationState()
        if args.workflow:
            state.workflow_name = args.workflow
        if not args.sync_via_git:
            if iteration_override is not None:
                state.step_index = max(0, iteration_override - 1)
                state.iteration = iteration_override
        return state

    router_cmd: list[str] | None = None
    if args.use_router and args.router_prompt:
        try:
            router_cmd = resolve_cmd(args.agent, args.claude_cmd, args.codex_cmd)
        except (RuntimeError, ValueError) as exc:
            print(f"[loop] ERROR: {exc}")
            return 2

    def _select_prompt(
        cmd: list[str] | None,
        logger,
        *,
        iteration_override: int | None = None,
    ) -> tuple[Path, OrchestrationState, str | None]:
        state = _router_state(iteration_override)
        allowlist = _router_allowlist()
        router_output = None
        if args.use_router and args.router_prompt:
            if cmd is None:
                raise RuntimeError("Router prompt requires agent CLI but no command was resolved.")
            router_prompt_path = resolve_prompt_path(args.router_prompt, cfg.prompts_dir)
            if not router_prompt_path.exists():
                raise FileNotFoundError(f"Router prompt file not found: {router_prompt_path}")
            router_output = run_router_prompt(cmd, router_prompt_path, logger)

        ctx = RouterContext(
            prompts_dir=cfg.prompts_dir,
            allowlist=allowlist,
            review_every_n_cycles=args.workflow_review_every_n,
            router_mode=args.router_mode,
            router_output=router_output,
            use_router=args.use_router,
        )
        selection = select_prompt(state, ctx, logger)
        return selection.prompt_path, state, selection.selected_prompt

    def _resolve_agent_cmd(
        role: str,
        prompt_path: Path,
        selected_prompt: str | None,
        logger,
    ):
        prompt_key = selected_prompt or prompt_key_from_path(prompt_path, cfg.prompts_dir)
        prompt_key = canonical_prompt_key(prompt_key, cfg.prompts_dir)
        selection = select_agent_cmd(
            role,
            prompt_key,
            agent_cfg,
            cli_role_map,
            cli_prompt_map,
            args.claude_cmd,
            args.codex_cmd,
        )
        logger(
            "[agent] role=%s prompt=%s agent=%s cmd=%s"
            % (role, prompt_key, selection.agent, " ".join(selection.cmd))
        )
        return selection

    def _pull_with_error(logger, ctx: str) -> bool:
        buf: list[str] = []
        def cap(m: str) -> None:
            logger(m)
            buf.append(m)
        ok = safe_pull(cap)
        if not ok:
            err_line = None
            for line in reversed(buf):
                low = line.lower()
                if ("error" in low) or ("fatal" in low) or ("would be overwritten" in low):
                    err_line = line
                    break
            if err_line:
                print(f"[sync] ERROR ({ctx}): {err_line}")
            else:
                print(f"[sync] ERROR ({ctx}): git pull failed; see iter log.")
            if args.allow_dirty:
                msg = f"[sync] WARNING ({ctx}): continuing due to --allow-dirty"
                logger(msg)
                print(msg)
                return True
        return ok

    # (reports auto-commit now shared via autocommit.autocommit_reports)

    # Branch guard / resolution (skip all git queries if --no-git)
    if args.no_git:
        branch_target = "local"
    elif args.branch:
        assert_on_branch(args.branch, lambda m: None)
        branch_target = args.branch
    else:
        branch_target = current_branch() or "local"

    # Always keep up to date (unless --no-git)
    if not args.no_git:
        ok_initial = _pull_with_error(logp, "initial")
        if not ok_initial:
            print("[sync] ERROR: git pull failed; see iter log for details (likely untracked-file collisions).")
            print("[sync] Remediation: move or remove the conflicting untracked files, then re-run the loop.")
            return 1

    for _ in range(args.sync_loops):
        # Check for exit signal in state file
        if args.state_file.exists():
            import json
            try:
                with open(args.state_file, "r") as f:
                    state_data = json.load(f)
                if state_data.get("exit"):
                    reason = state_data.get("exit_reason", "exit flag set")
                    print(f"[loop] Exiting: {reason}")
                    return 0
            except (json.JSONDecodeError, IOError):
                pass  # Continue if state file is malformed

        # Compute per-iteration log path (branch/prompt aware)
        if not args.no_git:
            ok_probe = _pull_with_error(lambda m: None, "probe")
            if not ok_probe:
                # Error line already printed
                return 1
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        st_for_log = OrchestrationState.read(str(args.state_file)) if args.state_file.exists() else OrchestrationState()
        iter_log = (
            args.logdir
            / branch_target.replace('/', '-')
            / "steps"
            / f"iter-{st_for_log.iteration:05d}_step-{st_for_log.step_index}_{ts}.log"
        )

        if args.sync_via_git:
            # Resume mode: if a local stamped handoff exists but isn't pushed yet, publish and skip work
            st_local = OrchestrationState.read(str(args.state_file))
            if (not _is_loop_turn(st_local) or st_local.status in {"complete", "failed"}) and has_unpushed_commits():
                def logp(msg: str) -> None:
                    iter_log.parent.mkdir(parents=True, exist_ok=True)
                    with open(iter_log, "a", encoding="utf-8") as f:
                        f.write(msg + "\n")
                logp("[sync] Detected local stamped handoff with unpushed commits; attempting push-only reconciliation.")
                if not push_with_rebase(branch_target, logp):
                    print("[sync] ERROR: failed to push local stamped handoff; resolve and retry.")
                    return 1
                continue

            args.state_file.parent.mkdir(parents=True, exist_ok=True)
            # Logger bound to this iteration
            def logp(msg: str) -> None:
                iter_log.parent.mkdir(parents=True, exist_ok=True)
                with open(iter_log, "a", encoding="utf-8") as f:
                    f.write(msg + "\n")

            logp("[SYNC] Waiting for step_index (odd) ...")
            start = time.time()
            while True:
                if not _pull_with_error(logp, "polling"):
                    # Error line already printed
                    return 1
                st = OrchestrationState.read(str(args.state_file))
                if _is_loop_turn(st):
                    break
                if args.max_wait_sec and (time.time() - start) > args.max_wait_sec:
                    logp("[SYNC] Timeout waiting for turn; exiting")
                    return 1
                time.sleep(args.poll_interval)

        try:
            prompt_path, _, selected_prompt = _select_prompt(
                router_cmd,
                logp,
                iteration_override=None if args.sync_via_git else (_ + 1),
            )
        except Exception as e:
            logp(f"ERROR: {e}")
            print(f"[sync] ERROR: {e}")
            return 2

        if not prompt_path.exists():
            logp(f"ERROR: prompt file not found: {prompt_path}")
            return 2
        try:
            selection = _resolve_agent_cmd("ralph", prompt_path, selected_prompt, logp)
        except Exception as e:
            logp(f"ERROR: {e}")
            print(f"[sync] ERROR: {e}")
            return 2

        if args.sync_via_git:
            st = OrchestrationState.read(str(args.state_file))
            if args.workflow:
                st.workflow_name = args.workflow
            if args.use_router:
                st.last_prompt = selected_prompt
            st.expected_step = selected_prompt
            st.status = "running"
            st.write(str(args.state_file))
            add([str(args.state_file)])
            commit(f"[SYNC i={st.iteration}] step={st.step_index} status=running")
            push_to(branch_target, logp)

        rc = tee_run(selection.cmd, prompt_path, iter_log)

        # Auto-commit reports evidence (before stamping) — constrained by extension and size caps
        if args.auto_commit_reports and not args.no_git:
            allowed = {e.strip().lower() for e in args.report_extensions.split(',') if e.strip()}
            autocommit_reports(
                allowed_extensions=allowed,
                max_file_bytes=args.max_report_file_bytes,
                max_total_bytes=args.max_report_total_bytes,
                force_add=args.force_add_reports,
                logger=logp,
                commit_message_prefix="RALPH AUTO: reports evidence — tests: not run",
                skip_predicate=_skip_reports,
                allowed_path_globs=report_path_globs,
            )

        # Complete handoff (stamp-first, idempotent) — only in sync mode
        if args.sync_via_git:
            sha = short_head()
            st = OrchestrationState.read(str(args.state_file))
            if args.workflow:
                st.workflow_name = args.workflow
            # STAMP FIRST (idempotent)
            if rc == 0:
                st.stamp(status="complete", increment_step=True, ralph_commit=sha)
                st.write(str(args.state_file))
                add([str(args.state_file)])
                commit(f"[SYNC i={st.iteration}] step={st.step_index} status=ok ralph_commit={sha}")
            else:
                st.stamp(status="failed", increment_step=False, ralph_commit=sha)
                st.write(str(args.state_file))
                add([str(args.state_file)])
                commit(f"[SYNC i={st.iteration}] step={st.step_index} status=fail ralph_commit={sha}")

            # Publish stamped state. If push fails, exit; restart resumes push.
            if not push_with_rebase(branch_target, logp):
                print("[sync] ERROR: failed to push stamped state; resolve and relaunch to resume push.")
                return 1
            if rc != 0:
                logp(f"Loop failed rc={rc}. Stamped failure and pushed; exiting.")
                return rc

        # Optional: push local commits from the loop (async hygiene) — skip if --no-git
        if not args.no_git and rc == 0 and has_unpushed_commits():
            try:
                push_to(branch_target, logp)
            except Exception as e:
                logp(f"WARNING: git push failed: {e}")
                return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
