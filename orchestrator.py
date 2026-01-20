from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .config import load_config, claude_cli_default
from .git_bus import assert_on_branch, current_branch, short_head
from .loop import main as loop_main
from .router import resolve_prompt_path
from .runner import RouterContext, run_router_prompt, run_turn, tee_run
from .state import OrchestrationState
from .supervisor import main as supervisor_main


Logger = Callable[[str], None]
StateWriter = Callable[[OrchestrationState], None]


def _make_logger(path: Path) -> Logger:
    path.parent.mkdir(parents=True, exist_ok=True)

    def _log(message: str) -> None:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")

    return _log


def _parse_allowlist(raw_value: str) -> list[str] | None:
    allowlist = [item.strip() for item in raw_value.split(",") if item.strip()]
    return allowlist or None


def _prompt_map(supervisor_prompt: str, main_prompt: str, reviewer_prompt: str) -> dict[str, str]:
    prompt_map = {"galph": supervisor_prompt, "ralph": main_prompt}
    if reviewer_prompt:
        prompt_map["reviewer"] = reviewer_prompt
    return prompt_map


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
    supervisor_prompt: str,
    main_prompt: str,
    reviewer_prompt: str,
    allowlist: list[str] | None,
    review_every_n: int,
    router_mode: str,
    router_output: Optional[str],
    use_router: bool,
) -> tuple[RouterContext, RouterContext]:
    prompt_map = _prompt_map(supervisor_prompt, main_prompt, reviewer_prompt)
    galph_ctx = RouterContext(
        prompts_dir=prompts_dir,
        prompt_map=prompt_map,
        allowlist=allowlist,
        review_every_n=review_every_n,
        router_mode=router_mode,
        router_output=router_output,
        use_router=use_router,
    )
    ralph_mode = router_mode if router_mode != "router_only" else "router_default"
    ralph_ctx = RouterContext(
        prompts_dir=prompts_dir,
        prompt_map=prompt_map,
        allowlist=allowlist,
        review_every_n=0 if use_router else 0,
        router_mode=ralph_mode,
        router_output=None,
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
) -> int:
    def _fail(role: str, message: str) -> int:
        if role == "galph":
            logger = galph_logger
        else:
            logger = ralph_logger
        logger(f"[orchestrator] ERROR: {message}")
        if role == "galph":
            state.stamp(expected_actor="galph", status="failed", galph_commit=short_head())
        else:
            state.stamp(expected_actor="ralph", status="failed", ralph_commit=short_head())
        state_writer(state)
        return 2

    state.expected_actor = "galph"
    state.status = "running-galph"
    state_writer(state)

    try:
        galph_result = run_turn("galph", state, galph_ctx, galph_executor, galph_logger)
    except Exception as exc:
        return _fail("galph", f"galph turn failed: {exc}")
    if galph_result.returncode != 0:
        state.stamp(expected_actor="galph", status="failed", galph_commit=short_head())
        state_writer(state)
        return galph_result.returncode

    state.stamp(expected_actor="ralph", status="waiting-ralph", galph_commit=short_head())
    state_writer(state)

    state.status = "running-ralph"
    state_writer(state)

    try:
        ralph_result = run_turn("ralph", state, ralph_ctx, ralph_executor, ralph_logger)
    except Exception as exc:
        return _fail("ralph", f"ralph turn failed: {exc}")
    if ralph_result.returncode != 0:
        state.stamp(expected_actor="ralph", status="failed", ralph_commit=short_head())
        state_writer(state)
        return ralph_result.returncode

    state.stamp(expected_actor="galph", status="complete", increment=True, ralph_commit=short_head())
    state_writer(state)
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


def _resolve_cmd(agent: str, claude_cmd: str, codex_cmd: str) -> list[str]:
    def _claude_cmd() -> list[str] | None:
        def _fmt(path: Path | str) -> list[str]:
            quoted = str(path).replace('"', '\\"')
            cmd_str = f'"{quoted}" -p --dangerously-skip-permissions --verbose --output-format text'
            return ["/bin/bash", "-lc", cmd_str]

        if claude_cmd:
            path = Path(claude_cmd)
            if path.is_file() and os.access(str(path), os.X_OK):
                return _fmt(path)
            which = shutil.which(claude_cmd)
            if which:
                return _fmt(which)

        default_cli = claude_cli_default()
        if default_cli:
            return _fmt(default_cli)
        return None

    def _codex_cmd() -> list[str] | None:
        codex_bin = shutil.which(codex_cmd) or codex_cmd
        if not codex_bin:
            return None
        return [
            codex_bin,
            "exec",
            "-m",
            "gpt-5-codex",
            "-c",
            "model_reasoning_effort=high",
            "--dangerously-bypass-approvals-and-sandbox",
        ]

    if agent == "claude":
        cmd = _claude_cmd()
        if not cmd:
            raise RuntimeError("Claude CLI not found; set --claude-cmd or choose --agent=codex.")
        return cmd
    if agent == "codex":
        cmd = _codex_cmd()
        if not cmd:
            raise RuntimeError("Codex CLI not found; set --codex-cmd or choose --agent=claude.")
        return cmd

    cmd = _claude_cmd()
    if cmd:
        return cmd
    cmd = _codex_cmd()
    if cmd:
        return cmd
    raise RuntimeError("Neither Claude nor Codex CLI could be resolved; configure --claude-cmd/--codex-cmd.")


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

    try:
        cmd = _resolve_cmd(args.agent, args.claude_cmd, args.codex_cmd)
    except RuntimeError as exc:
        print(f"[orchestrator] ERROR: {exc}")
        return 2

    main_prompt_name = args.router_prompt_main or args.prompt_main
    if not main_prompt_name.endswith(".md"):
        main_prompt_name = f"{main_prompt_name}.md"
    main_prompt_stem = Path(main_prompt_name).stem

    for _ in range(args.sync_loops):
        should_exit, reason = _check_exit_signal(state_file)
        if should_exit:
            print(f"[orchestrator] Exiting: {reason}")
            return 0

        iter_num = state.iteration
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        galph_log = args.logdir / branch_target.replace("/", "-") / "galph" / f"iter-{iter_num:05d}_{ts}.log"
        ralph_log = (
            args.logdir
            / branch_target.replace("/", "-")
            / "ralph"
            / f"iter-{iter_num:05d}_{ts}_{main_prompt_stem}.log"
        )
        galph_logger = _make_logger(galph_log)
        ralph_logger = _make_logger(ralph_log)

        def _write_state(st: OrchestrationState) -> None:
            st.write(str(state_file))

        def _fail(role: str, message: str) -> int:
            logger = galph_logger if role == "galph" else ralph_logger
            logger(f"[orchestrator] ERROR: {message}")
            if role == "galph":
                state.stamp(expected_actor="galph", status="failed", galph_commit=short_head())
            else:
                state.stamp(expected_actor="ralph", status="failed", ralph_commit=short_head())
            _write_state(state)
            return 2

        router_output = None
        if args.use_router and args.router_prompt:
            router_prompt_path = resolve_prompt_path(args.router_prompt, cfg.prompts_dir)
            if not router_prompt_path.exists():
                return _fail("galph", f"router prompt not found: {router_prompt_path}")
            try:
                router_output = run_router_prompt(cmd, router_prompt_path, galph_logger)
            except Exception as exc:
                return _fail("galph", f"router prompt failed: {exc}")

        allowlist = _parse_allowlist(args.router_allowlist)
        galph_ctx, ralph_ctx = build_combined_contexts(
            prompts_dir=cfg.prompts_dir,
            supervisor_prompt=args.router_prompt_supervisor or args.prompt_supervisor,
            main_prompt=args.router_prompt_main or args.prompt_main,
            reviewer_prompt=args.router_prompt_reviewer or args.prompt_reviewer,
            allowlist=allowlist,
            review_every_n=args.router_review_every_n,
            router_mode=args.router_mode,
            router_output=router_output,
            use_router=args.use_router,
        )

        def _galph_exec(prompt_path: Path) -> int:
            return tee_run(cmd, prompt_path, galph_log)

        def _ralph_exec(prompt_path: Path) -> int:
            return tee_run(cmd, prompt_path, ralph_log)

        rc = run_combined_iteration(
            state=state,
            galph_ctx=galph_ctx,
            ralph_ctx=ralph_ctx,
            galph_executor=_galph_exec,
            ralph_executor=_ralph_exec,
            galph_logger=galph_logger,
            ralph_logger=ralph_logger,
            state_writer=_write_state,
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
        default=os.getenv("ORCHESTRATOR_AGENT", "auto"),
        help="Model CLI used for combined orchestrator (auto: prefer Claude, fallback Codex).",
    )
    ap.add_argument("--prompt-supervisor", type=str, default=os.getenv("SUPERVISOR_PROMPT", cfg.supervisor_prompt))
    ap.add_argument("--prompt-main", type=str, default=os.getenv("LOOP_PROMPT", cfg.main_prompt))
    ap.add_argument("--prompt-reviewer", type=str, default=os.getenv("REVIEWER_PROMPT", cfg.reviewer_prompt))
    ap.add_argument("--use-router", dest="use_router", action="store_true")
    ap.add_argument("--no-router", dest="use_router", action="store_false")
    ap.set_defaults(use_router=cfg.router_enabled)
    ap.add_argument("--router-prompt", type=str, default=os.getenv("ROUTER_PROMPT", cfg.router_prompt or ""))
    ap.add_argument(
        "--router-review-every-n",
        type=int,
        default=int(os.getenv("ROUTER_REVIEW_EVERY_N", str(cfg.router_review_every_n))),
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
        if args.role == "galph":
            return supervisor_main()
        return loop_main()

    return _run_combined(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
