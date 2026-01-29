from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePath, PurePosixPath
from typing import Callable, Optional

from .autocommit import autocommit_docs, autocommit_reports, autocommit_tracked_outputs
from .agent_dispatch import (
    AgentConfig,
    canonical_prompt_key,
    normalize_prompt_map,
    normalize_role_map,
    parse_agent_map,
    prompt_key_from_path,
    resolve_cmd,
    select_agent_cmd,
    normalize_role_key,
)
from .config import load_config
from .git_bus import assert_on_branch, current_branch, short_head
from .loop import main as loop_main
from .router import resolve_prompt_path
from .runner import RouterContext, run_router_prompt, run_turn, tee_run
from .state import OrchestrationState
from .supervisor import main as supervisor_main


Logger = Callable[[str], None]
StateWriter = Callable[[OrchestrationState], None]


@dataclass(frozen=True)
class CombinedAutoCommitConfig:
    auto_commit_docs: bool
    auto_commit_reports: bool
    auto_commit_tracked_outputs: bool
    dry_run: bool
    no_git: bool
    doc_whitelist: list[str]
    max_autocommit_bytes: int
    report_extensions: set[str]
    report_path_globs: tuple[str, ...]
    max_report_file_bytes: int
    max_report_total_bytes: int
    force_add_reports: bool
    tracked_output_globs: list[str]
    tracked_output_extensions: set[str]
    max_tracked_output_file_bytes: int
    max_tracked_output_total_bytes: int
    logdir_prefix_parts: tuple[str, ...]
    state_file: Path


def _build_autocommit_config(args, cfg) -> CombinedAutoCommitConfig:
    report_path_globs = tuple(p.strip() for p in args.report_path_globs.split(",") if p.strip())
    logdir_prefix_parts = tuple(part for part in PurePath(args.logdir).parts if part not in {"", "."})
    doc_whitelist = [p.strip() for p in args.autocommit_whitelist.split(",") if p.strip()]
    tracked_globs = [p.strip() for p in args.tracked_output_globs.split(",") if p.strip()]
    tracked_exts = {e.strip().lower() for e in args.tracked_output_extensions.split(",") if e.strip()}
    report_exts = {e.strip().lower() for e in args.report_extensions.split(",") if e.strip()}
    return CombinedAutoCommitConfig(
        auto_commit_docs=args.auto_commit_docs,
        auto_commit_reports=args.auto_commit_reports,
        auto_commit_tracked_outputs=args.auto_commit_tracked_outputs,
        dry_run=args.commit_dry_run,
        no_git=args.no_git,
        doc_whitelist=doc_whitelist,
        max_autocommit_bytes=int(args.max_autocommit_bytes),
        report_extensions=report_exts or set(cfg.report_extensions),
        report_path_globs=report_path_globs,
        max_report_file_bytes=int(args.max_report_file_bytes),
        max_report_total_bytes=int(args.max_report_total_bytes),
        force_add_reports=args.force_add_reports,
        tracked_output_globs=tracked_globs,
        tracked_output_extensions=tracked_exts or set(cfg.tracked_output_extensions),
        max_tracked_output_file_bytes=int(args.max_tracked_output_file_bytes),
        max_tracked_output_total_bytes=int(args.max_tracked_output_total_bytes),
        logdir_prefix_parts=logdir_prefix_parts,
        state_file=args.state_file,
    )


def _format_iteration_tag(iteration: Optional[int]) -> str:
    if iteration is None:
        return ""
    return f" (iter={iteration:05d})"


def _role_commit_prefix(role: str) -> str:
    return "SUPERVISOR AUTO" if role == "galph" else "RALPH AUTO"


def _format_prompt_tag(prompt_name: Optional[str]) -> str:
    if not prompt_name:
        return ""
    return f" (prompt={prompt_name})"


def run_combined_autocommit(
    *,
    role: str,
    logger: Logger,
    config: CombinedAutoCommitConfig,
    iteration: Optional[int] = None,
    prompt_name: Optional[str] = None,
) -> None:
    if config.no_git:
        logger(f"[autocommit:{role}] Skipping auto-commit (--no-git)")
        return
    if config.dry_run:
        logger(f"[autocommit:{role}] DRY-RUN: no git add/commit")

    def _within(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
        return bool(prefix) and parts[:len(prefix)] == prefix

    def _skip_reports(path: str) -> bool:
        parts = PurePosixPath(path).parts
        if _within(parts, config.logdir_prefix_parts):
            return True
        if parts and parts[0] == "tmp":
            return True
        return False

    iter_tag = _format_iteration_tag(iteration)
    role_prefix = _role_commit_prefix(role)
    prompt_tag = _format_prompt_tag(prompt_name)
    prefix = f"{role_prefix}{prompt_tag}{iter_tag}"

    if config.auto_commit_reports:
        try:
            autocommit_reports(
                allowed_extensions=config.report_extensions,
                max_file_bytes=config.max_report_file_bytes,
                max_total_bytes=config.max_report_total_bytes,
                force_add=config.force_add_reports,
                logger=logger,
                commit_message_prefix=f"{prefix}: reports evidence — tests: not run",
                skip_predicate=_skip_reports,
                allowed_path_globs=config.report_path_globs,
                dry_run=config.dry_run,
            )
        except Exception as exc:
            logger(f"[autocommit:{role}] WARNING: reports auto-commit failed: {exc}")

    if config.auto_commit_tracked_outputs:
        try:
            autocommit_tracked_outputs(
                tracked_output_globs=config.tracked_output_globs,
                tracked_output_extensions=config.tracked_output_extensions,
                max_file_bytes=config.max_tracked_output_file_bytes,
                max_total_bytes=config.max_tracked_output_total_bytes,
                logger=logger,
                commit_message_prefix=f"{prefix}: tracked outputs — tests: not run",
                dry_run=config.dry_run,
            )
        except Exception as exc:
            logger(f"[autocommit:{role}] WARNING: tracked outputs auto-commit failed: {exc}")

    if config.auto_commit_docs:
        try:
            _, _, forbidden = autocommit_docs(
                whitelist_globs=config.doc_whitelist,
                max_file_bytes=config.max_autocommit_bytes,
                logger=logger,
                commit_message_prefix=f"{prefix}: doc/meta hygiene — tests: not run",
                dry_run=config.dry_run,
                ignore_paths=[str(config.state_file)],
            )
            if forbidden:
                logger(
                    f"[autocommit:{role}] WARNING: non-whitelist dirty paths remain: "
                    + ", ".join(forbidden)
                )
        except Exception as exc:
            logger(f"[autocommit:{role}] WARNING: doc/meta auto-commit failed: {exc}")


def _make_logger(path: Path) -> Logger:
    path.parent.mkdir(parents=True, exist_ok=True)

    def _log(message: str) -> None:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")

    return _log


def _parse_allowlist(raw_value: str) -> list[str] | None:
    allowlist = [item.strip() for item in raw_value.split(",") if item.strip()]
    return allowlist or None


def _argv_has_flag(argv: list[str], flag: str) -> bool:
    return any(token == flag or token.startswith(f"{flag}=") for token in argv)


def _argv_flag_value(argv: list[str], flag: str) -> str | None:
    for idx, token in enumerate(argv):
        if token == flag and idx + 1 < len(argv):
            return argv[idx + 1]
        if token.startswith(f"{flag}="):
            return token.split("=", 1)[1]
    return None


def _apply_role_prompt_override(role: str) -> None:
    if _argv_has_flag(sys.argv, "--prompt"):
        return
    if role == "galph":
        value = _argv_flag_value(sys.argv, "--prompt-supervisor")
        if value:
            os.environ["SUPERVISOR_PROMPT"] = value
    elif role == "ralph":
        value = _argv_flag_value(sys.argv, "--prompt-main")
        if value:
            os.environ["LOOP_PROMPT"] = value


def build_combined_contexts(
    *,
    prompts_dir: Path,
    allowlist: list[str] | None,
    review_every_n_cycles: int,
    router_mode: str,
    router_output: Optional[str],
    use_router: bool,
) -> tuple[RouterContext, RouterContext]:
    galph_ctx = RouterContext(
        prompts_dir=prompts_dir,
        allowlist=allowlist,
        review_every_n_cycles=review_every_n_cycles,
        router_mode=router_mode,
        router_output=router_output,
        use_router=use_router,
    )
    ralph_ctx = RouterContext(
        prompts_dir=prompts_dir,
        allowlist=allowlist,
        review_every_n_cycles=review_every_n_cycles,
        router_mode=router_mode,
        router_output=router_output,
        use_router=use_router,
    )
    return galph_ctx, ralph_ctx


def run_combined_iteration(
    *,
    state: OrchestrationState,
    galph_ctx: RouterContext,
    ralph_ctx: RouterContext,
    galph_executor,
    ralph_executor,
    galph_logger: Logger,
    ralph_logger: Logger,
    state_writer: StateWriter,
    post_turn: Optional[Callable[[str, OrchestrationState, Logger, str], None]] = None,
) -> int:
    def _fail(role: str, message: str) -> int:
        if role == "galph":
            logger = galph_logger
        else:
            logger = ralph_logger
        logger(f"[orchestrator] ERROR: {message}")
        if role == "galph":
            state.stamp(status="failed", galph_commit=short_head())
        else:
            state.stamp(status="failed", ralph_commit=short_head())
        state_writer(state)
        return 2

    state.status = "running"
    state_writer(state)

    try:
        galph_result = run_turn(state, galph_ctx, galph_executor, galph_logger)
    except Exception as exc:
        return _fail("galph", f"galph turn failed: {exc}")
    if galph_result.returncode != 0:
        state.stamp(status="failed", galph_commit=short_head())
        state_writer(state)
        return galph_result.returncode

    state.stamp(status="waiting-next", increment_step=True, galph_commit=short_head())
    state_writer(state)
    if post_turn:
        post_turn("galph", state, galph_logger, galph_result.selected_prompt)

    state.status = "running"
    state_writer(state)

    try:
        ralph_result = run_turn(state, ralph_ctx, ralph_executor, ralph_logger)
    except Exception as exc:
        return _fail("ralph", f"ralph turn failed: {exc}")
    if ralph_result.returncode != 0:
        state.stamp(status="failed", ralph_commit=short_head())
        state_writer(state)
        return ralph_result.returncode

    state.stamp(status="complete", increment_step=True, ralph_commit=short_head())
    state_writer(state)
    if post_turn:
        post_turn("ralph", state, ralph_logger, ralph_result.selected_prompt)
    return 0


def _check_exit_signal(state_file: Path) -> tuple[bool, str]:
    if state_file.exists():
        import json

        try:
            with open(state_file, "r", encoding="utf-8") as handle:
                state_data = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return False, ""
        if state_data.get("exit"):
            return True, state_data.get("exit_reason", "exit flag set")
    return False, ""


def _run_combined(args, cfg) -> int:
    if args.sync_via_git:
        print("[orchestrator] ERROR: combined mode does not support --sync-via-git.")
        return 2

    if args.branch:
        assert_on_branch(args.branch, lambda m: None)
        branch_target = args.branch
    else:
        branch_target = current_branch() or "local"

    state_file = args.state_file
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state = OrchestrationState.read(str(state_file)) if state_file.exists() else OrchestrationState()
    if args.workflow:
        state.workflow_name = args.workflow

    try:
        cli_role_map = parse_agent_map(args.agent_role, normalize_role_key)
        cli_prompt_map = parse_agent_map(
            args.agent_prompt,
            lambda key: canonical_prompt_key(key, cfg.prompts_dir),
        )
    except ValueError as exc:
        print(f"[orchestrator] ERROR: {exc}")
        return 2

    agent_cfg = AgentConfig(
        default_agent=args.agent,
        role_map=normalize_role_map(cfg.agent_roles),
        prompt_map=normalize_prompt_map(cfg.agent_prompts, cfg.prompts_dir),
        prompts_dir=cfg.prompts_dir,
    )
    try:
        router_cmd = resolve_cmd(args.agent, args.claude_cmd, args.codex_cmd)
    except (RuntimeError, ValueError) as exc:
        print(f"[orchestrator] ERROR: {exc}")
        return 2

    auto_commit_cfg = _build_autocommit_config(args, cfg)

    for _ in range(args.sync_loops):
        should_exit, reason = _check_exit_signal(state_file)
        if should_exit:
            print(f"[orchestrator] Exiting: {reason}")
            return 0

        iter_num = state.iteration
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        galph_log = (
            args.logdir
            / branch_target.replace("/", "-")
            / "steps"
            / f"iter-{iter_num:05d}_step-{state.step_index}_{ts}.log"
        )
        ralph_log = (
            args.logdir
            / branch_target.replace("/", "-")
            / "steps"
            / f"iter-{iter_num:05d}_step-{state.step_index + 1}_{ts}.log"
        )
        galph_logger = _make_logger(galph_log)
        ralph_logger = _make_logger(ralph_log)

        def _write_state(st: OrchestrationState) -> None:
            st.write(str(state_file))

        def _fail(role: str, message: str) -> int:
            logger = galph_logger if role == "galph" else ralph_logger
            logger(f"[orchestrator] ERROR: {message}")
            if role == "galph":
                state.stamp(status="failed", galph_commit=short_head())
            else:
                state.stamp(status="failed", ralph_commit=short_head())
            _write_state(state)
            return 2

        router_output = None
        if args.use_router and args.router_prompt:
            router_prompt_path = resolve_prompt_path(args.router_prompt, cfg.prompts_dir)
            if not router_prompt_path.exists():
                return _fail("galph", f"router prompt not found: {router_prompt_path}")
            try:
                router_output = run_router_prompt(router_cmd, router_prompt_path, galph_logger)
            except Exception as exc:
                return _fail("galph", f"router prompt failed: {exc}")

        allowlist = _parse_allowlist(args.router_allowlist)
        galph_ctx, ralph_ctx = build_combined_contexts(
            prompts_dir=cfg.prompts_dir,
            allowlist=allowlist,
            review_every_n_cycles=args.workflow_review_every_n,
            router_mode=args.router_mode,
            router_output=router_output,
            use_router=args.use_router,
        )

        def _exec_for(role: str, prompt_path: Path, logger: Logger, log_path: Path) -> int:
            prompt_key = prompt_key_from_path(prompt_path, cfg.prompts_dir)
            try:
                selection = select_agent_cmd(
                    role,
                    prompt_key,
                    agent_cfg,
                    cli_role_map,
                    cli_prompt_map,
                    args.claude_cmd,
                    args.codex_cmd,
                )
            except Exception as exc:
                logger(f"[agent] ERROR: {exc}")
                raise
            logger(
                "[agent] role=%s prompt=%s agent=%s cmd=%s"
                % (role, prompt_key, selection.agent, " ".join(selection.cmd))
            )
            pty_mode = os.getenv("ORCHESTRATION_PTY_MODE", "auto").strip().lower()
            if pty_mode == "always":
                use_pty = True
            elif pty_mode == "never":
                use_pty = False
            elif selection.agent == "claude":
                use_pty = False
            else:
                use_pty = None
            return tee_run(selection.cmd, prompt_path, log_path, use_pty=use_pty)

        def _galph_exec(prompt_path: Path) -> int:
            return _exec_for("galph", prompt_path, galph_logger, galph_log)

        def _ralph_exec(prompt_path: Path) -> int:
            return _exec_for("ralph", prompt_path, ralph_logger, ralph_log)

        def _post_turn(role: str, _: OrchestrationState, logger: Logger, prompt_name: str) -> None:
            run_combined_autocommit(
                role=role,
                logger=logger,
                config=auto_commit_cfg,
                iteration=iter_num,
                prompt_name=prompt_name,
            )

        rc = run_combined_iteration(
            state=state,
            galph_ctx=galph_ctx,
            ralph_ctx=ralph_ctx,
            galph_executor=_galph_exec,
            ralph_executor=_ralph_exec,
            galph_logger=galph_logger,
            ralph_logger=ralph_logger,
            state_writer=_write_state,
            post_turn=_post_turn,
        )
        if rc != 0:
            return rc

        time.sleep(args.poll_interval)

    return 0


def main() -> int:
    cfg = load_config(warn_missing=False)

    ap = argparse.ArgumentParser(description="Combined orchestrator (galph + ralph) with optional role-gated sync.")
    ap.add_argument("--mode", choices=["combined", "role"], default=os.getenv("ORCHESTRATOR_MODE", "combined"))
    ap.add_argument("--role", choices=["galph", "ralph"], default=os.getenv("ORCHESTRATOR_ROLE", ""))
    ap.add_argument("--sync-via-git", action="store_true", help="Enable sync-via-git behavior for role mode")
    ap.add_argument("--no-git", action="store_true", help="Disable git operations (combined mode only)")
    ap.add_argument("--commit-dry-run", action="store_true",
                    help="Log auto-commit actions without staging/committing")
    ap.add_argument("--sync-loops", type=int, default=int(os.getenv("SYNC_LOOPS", 20)))
    ap.add_argument("--poll-interval", type=int, default=int(os.getenv("POLL_INTERVAL", 5)))
    ap.add_argument("--state-file", type=Path, default=Path(os.getenv("STATE_FILE", str(cfg.state_file))))
    ap.add_argument("--logdir", type=Path, default=cfg.logs_dir)
    ap.add_argument("--branch", type=str, default=os.getenv("ORCHESTRATION_BRANCH", ""))
    ap.add_argument("--claude-cmd", type=str, default=os.getenv("CLAUDE_CMD", ""))
    ap.add_argument("--codex-cmd", type=str, default=os.getenv("CODEX_CMD", "codex"))
    ap.add_argument(
        "--agent",
        type=str,
        choices=["auto", "claude", "codex"],
        default=os.getenv("ORCHESTRATOR_AGENT", cfg.agent_default),
        help="Model CLI used for combined orchestrator (auto: prefer Claude, fallback Codex).",
    )
    ap.add_argument("--agent-role", type=str, default=os.getenv("ORCHESTRATOR_AGENT_ROLE", ""))
    ap.add_argument("--agent-prompt", type=str, default=os.getenv("ORCHESTRATOR_AGENT_PROMPT", ""))
    ap.add_argument("--prompt-supervisor", type=str, default=os.getenv("SUPERVISOR_PROMPT", cfg.supervisor_prompt))
    ap.add_argument("--prompt-main", type=str, default=os.getenv("LOOP_PROMPT", cfg.main_prompt))
    ap.add_argument("--prompt-reviewer", type=str, default=os.getenv("REVIEWER_PROMPT", cfg.reviewer_prompt))
    ap.add_argument("--use-router", dest="use_router", action="store_true")
    ap.add_argument("--no-router", dest="use_router", action="store_false")
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
    ap.add_argument("--router-prompt", type=str, default=os.getenv("ROUTER_PROMPT", cfg.router_prompt or ""))
    ap.add_argument(
        "--router-review-every-n",
        dest="workflow_review_every_n",
        type=int,
        default=argparse.SUPPRESS,
        help="Deprecated alias for --workflow-review-every-n.",
    )
    ap.add_argument(
        "--router-allowlist",
        type=str,
        default=os.getenv("ROUTER_ALLOWLIST", ",".join(cfg.router_allowlist)),
    )
    ap.add_argument(
        "--router-prompt-supervisor",
        type=str,
        default=os.getenv("ROUTER_PROMPT_SUPERVISOR", ""),
    )
    ap.add_argument(
        "--router-prompt-main",
        type=str,
        default=os.getenv("ROUTER_PROMPT_MAIN", ""),
    )
    ap.add_argument(
        "--router-prompt-reviewer",
        type=str,
        default=os.getenv("ROUTER_PROMPT_REVIEWER", ""),
    )
    ap.add_argument(
        "--router-mode",
        type=str,
        default=os.getenv("ROUTER_MODE", cfg.router_mode),
    )
    # Auto-commit doc/meta hygiene (combined mode)
    ap.add_argument("--auto-commit-docs", dest="auto_commit_docs", action="store_true",
                    help="Auto-stage+commit doc/meta whitelist when dirty (default: on)")
    ap.add_argument("--no-auto-commit-docs", dest="auto_commit_docs", action="store_false",
                    help="Disable auto commit of doc/meta whitelist")
    ap.set_defaults(auto_commit_docs=True)
    ap.add_argument("--autocommit-whitelist", type=str,
                    default=",".join(cfg.doc_whitelist),
                    help="Comma-separated glob whitelist for auto-commit (doc/meta only)")
    ap.add_argument("--max-autocommit-bytes", type=int, default=int(os.getenv("MAX_AUTOCOMMIT_BYTES", "1048576")),
                    help="Maximum per-file size (bytes) eligible for auto-commit")
    # Reports auto-commit (combined mode)
    ap.add_argument("--auto-commit-reports", dest="auto_commit_reports", action="store_true",
                    help="Auto-stage+commit report artifacts by file extension after each turn (default: on)")
    ap.add_argument("--no-auto-commit-reports", dest="auto_commit_reports", action="store_false",
                    help="Disable auto commit of report artifacts")
    ap.set_defaults(auto_commit_reports=True)
    ap.add_argument("--report-extensions", type=str,
                    default=os.getenv("SUPERVISOR_REPORT_EXTENSIONS", ",".join(cfg.report_extensions)),
                    help="Comma-separated list of allowed report file extensions (lowercase, with dots)")
    ap.add_argument("--max-report-file-bytes", type=int, default=int(os.getenv("SUPERVISOR_MAX_REPORT_FILE_BYTES", "5242880")),
                    help="Maximum per-file size (bytes) eligible for reports auto-commit (default 5 MiB)")
    ap.add_argument("--max-report-total-bytes", type=int, default=int(os.getenv("SUPERVISOR_MAX_REPORT_TOTAL_BYTES", "20971520")),
                    help="Maximum total size (bytes) staged per iteration for reports (default 20 MiB)")
    ap.add_argument("--force-add-reports", dest="force_add_reports", action="store_true",
                    help="Force-add report files even if ignored (.gitignore) (default: on)")
    ap.add_argument("--no-force-add-reports", dest="force_add_reports", action="store_false",
                    help="Do not force-add ignored report files")
    ap.set_defaults(force_add_reports=True)
    ap.add_argument("--report-path-globs", type=str,
                    default=os.getenv("SUPERVISOR_REPORT_PATH_GLOBS", ""),
                    help="Comma-separated glob allowlist for report auto-commit paths (default: none)")
    # Tracked outputs auto-commit (combined mode)
    ap.add_argument("--auto-commit-tracked-outputs", dest="auto_commit_tracked_outputs", action="store_true",
                    help="Auto-stage+commit modified tracked outputs when within limits (default: on)")
    ap.add_argument("--no-auto-commit-tracked-outputs", dest="auto_commit_tracked_outputs", action="store_false",
                    help="Disable auto commit of modified tracked outputs")
    ap.set_defaults(auto_commit_tracked_outputs=True)
    ap.add_argument("--tracked-output-globs", type=str,
                    default=os.getenv("SUPERVISOR_TRACKED_OUTPUT_GLOBS", ",".join(cfg.tracked_output_globs)),
                    help="Comma-separated glob allowlist for tracked output paths (default targets test fixtures)")
    ap.add_argument("--tracked-output-extensions", type=str,
                    default=os.getenv("SUPERVISOR_TRACKED_OUTPUT_EXTENSIONS", ",".join(cfg.tracked_output_extensions)),
                    help="Comma-separated list of allowed file extensions for tracked outputs")
    ap.add_argument("--max-tracked-output-file-bytes", type=int,
                    default=int(os.getenv("SUPERVISOR_MAX_TRACKED_OUTPUT_FILE_BYTES", str(32 * 1024 * 1024))),
                    help="Maximum per-file size (bytes) eligible for tracked outputs auto-commit (default 32 MiB)")
    ap.add_argument("--max-tracked-output-total-bytes", type=int,
                    default=int(os.getenv("SUPERVISOR_MAX_TRACKED_OUTPUT_TOTAL_BYTES", str(100 * 1024 * 1024))),
                    help="Maximum total size (bytes) staged per iteration for tracked outputs (default 100 MiB)")

    args, _ = ap.parse_known_args()

    if not args.router_prompt_supervisor:
        args.router_prompt_supervisor = args.prompt_supervisor
    if not args.router_prompt_main:
        args.router_prompt_main = args.prompt_main
    if not args.router_prompt_reviewer:
        args.router_prompt_reviewer = args.prompt_reviewer

    if args.mode == "role":
        if not args.role:
            print("[orchestrator] ERROR: --role is required when --mode=role.")
            return 2
        if not args.sync_via_git:
            print("[orchestrator] ERROR: role mode requires --sync-via-git.")
            return 2
        _apply_role_prompt_override(args.role)
        if args.workflow:
            os.environ["ORCHESTRATION_WORKFLOW"] = args.workflow
        if hasattr(args, "workflow_review_every_n"):
            os.environ["ORCHESTRATION_WORKFLOW_REVIEW_EVERY_N"] = str(args.workflow_review_every_n)
        if args.role == "galph":
            return supervisor_main()
        return loop_main()

    return _run_combined(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
