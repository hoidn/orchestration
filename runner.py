from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from subprocess import PIPE, Popen, run
from typing import Callable, Iterable, Optional

from .router import RouterDecision, log_router_decision, resolve_prompt_path, select_prompt_with_mode
from .state import OrchestrationState


PromptExecutor = Callable[[Path], int]
Logger = Callable[[str], None]


@dataclass(frozen=True)
class RouterContext:
    prompts_dir: Path
    allowlist: Optional[Iterable[str]]
    review_every_n_cycles: int
    router_mode: str | None = None
    router_output: Optional[str] = None
    use_router: bool = False


@dataclass(frozen=True)
class PromptSelection:
    prompt_path: Path
    selected_prompt: str
    decision: Optional[RouterDecision] = None


@dataclass(frozen=True)
class TurnResult:
    state: OrchestrationState
    returncode: int
    prompt_path: Path
    selected_prompt: str
    decision: Optional[RouterDecision] = None


def log_file(prefix: str, tmp_dir: Path = Path("tmp")) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / f"{prefix}{ts}.txt"
    latest = tmp_dir / f"{prefix}latest.txt"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(path.name)
    except Exception:
        pass
    return path


def tee_run(cmd: list[str], stdin_file: Path | None, log_path: Path) -> int:
    """Run command with output tee'd to a log file using non-blocking I/O."""
    import os
    import pty
    import select
    import time

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as flog:
        flog.write(f"$ {' '.join(cmd)}\n")
        flog.flush()

        fin = open(stdin_file, "rb") if stdin_file else open("/dev/null", "rb")
        try:
            use_pty = os.getenv("ORCHESTRATION_USE_PTY", "1").strip().lower() not in {"0", "false", "no"}
            prompt_label = str(stdin_file.name) if isinstance(stdin_file, Path) else "<stdin>"
            heartbeat_raw = os.getenv("ORCHESTRATION_PROMPT_HEARTBEAT_SECS", "10").strip()
            timeout_raw = os.getenv("ORCHESTRATION_PROMPT_TIMEOUT_SECS", "0").strip()
            try:
                heartbeat_secs = float(heartbeat_raw) if heartbeat_raw else 0.0
            except ValueError:
                heartbeat_secs = 0.0
            heartbeat_secs = max(0.0, heartbeat_secs)
            try:
                timeout_secs = float(timeout_raw) if timeout_raw else 0.0
            except ValueError:
                timeout_secs = 0.0
            timeout_secs = max(0.0, timeout_secs)
            start_ts = time.time()
            last_output = start_ts
            last_beat = start_ts
            timed_out = False

            def _write_chunk(chunk: bytes) -> None:
                nonlocal last_output
                if not chunk:
                    return
                last_output = time.time()
                text = chunk.decode("utf-8", errors="replace")
                sys.stdout.write(text)
                sys.stdout.flush()
                flog.write(text)
                flog.flush()

            def _emit_heartbeat(force: bool = False) -> None:
                nonlocal last_beat
                if heartbeat_secs <= 0:
                    return
                now = time.time()
                if not force:
                    if (now - last_output) < heartbeat_secs:
                        return
                    if (now - last_beat) < heartbeat_secs:
                        return
                elapsed = int(now - start_ts)
                msg = (
                    f"[runner] still running ({elapsed}s elapsed) "
                    f"prompt={prompt_label} log={log_path}\n"
                )
                sys.stdout.write(msg)
                sys.stdout.flush()
                flog.write(msg)
                flog.flush()
                last_beat = now

            def _check_timeout(proc: Popen) -> None:
                nonlocal timed_out
                if timeout_secs <= 0 or timed_out:
                    return
                now = time.time()
                if (now - start_ts) < timeout_secs:
                    return
                timed_out = True
                msg = (
                    f"[runner] timeout ({int(now - start_ts)}s elapsed) "
                    f"prompt={prompt_label} log={log_path}; terminating\n"
                )
                sys.stdout.write(msg)
                sys.stdout.flush()
                flog.write(msg)
                flog.flush()
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

            if heartbeat_secs > 0:
                msg = f"[runner] start prompt={prompt_label} log={log_path}\n"
                sys.stdout.write(msg)
                sys.stdout.flush()
                flog.write(msg)
                flog.flush()

            if use_pty:
                master_fd, slave_fd = pty.openpty()
                try:
                    proc = Popen(cmd, stdin=fin, stdout=slave_fd, stderr=slave_fd, bufsize=0, close_fds=True)
                finally:
                    os.close(slave_fd)

                try:
                    while True:
                        readable, _, _ = select.select([master_fd], [], [], 0.1)
                        if master_fd in readable:
                            try:
                                chunk = os.read(master_fd, 4096)
                            except OSError:
                                chunk = b""
                            if chunk:
                                _write_chunk(chunk)
                            elif proc.poll() is not None:
                                break
                        if proc.poll() is not None and not readable:
                            try:
                                chunk = os.read(master_fd, 4096)
                            except OSError:
                                chunk = b""
                            if chunk:
                                _write_chunk(chunk)
                            else:
                                break
                        _emit_heartbeat()
                        _check_timeout(proc)
                finally:
                    os.close(master_fd)

                return proc.returncode if proc.returncode is not None else (124 if timed_out else 0)

            proc = Popen(cmd, stdin=fin, stdout=PIPE, stderr=PIPE, bufsize=0)

            stdout_fd = proc.stdout.fileno() if proc.stdout else -1
            stderr_fd = proc.stderr.fileno() if proc.stderr else -1

            while True:
                readable, _, _ = select.select(
                    [fd for fd in [stdout_fd, stderr_fd] if fd >= 0],
                    [],
                    [],
                    0.1,
                )

                if stdout_fd in readable and proc.stdout:
                    chunk = proc.stdout.read(4096)
                    if chunk:
                        _write_chunk(chunk)

                if stderr_fd in readable and proc.stderr:
                    chunk = proc.stderr.read(4096)
                    if chunk:
                        last_output = time.time()
                        text = chunk.decode("utf-8", errors="replace")
                        sys.stderr.write(text)
                        sys.stderr.flush()
                        flog.write(text)
                        flog.flush()

                if proc.poll() is not None:
                    if proc.stdout:
                        remaining = proc.stdout.read()
                        if remaining:
                            _write_chunk(remaining)
                    if proc.stderr:
                        remaining = proc.stderr.read()
                        if remaining:
                            text = remaining.decode("utf-8", errors="replace")
                            sys.stderr.write(text)
                            sys.stderr.flush()
                            flog.write(text)
                    break
                _emit_heartbeat()
                _check_timeout(proc)

            return proc.returncode if proc.returncode is not None else (124 if timed_out else 0)
        finally:
            fin.close()


def run_router_prompt(cmd: list[str], prompt_path: Path, logger: Logger) -> str:
    logger(f"[router] Running router prompt: {prompt_path}")
    try:
        with open(prompt_path, "rb") as fin:
            cp = run(cmd, stdin=fin, stdout=PIPE, stderr=PIPE)
    except Exception as exc:
        raise RuntimeError(f"Router prompt execution failed: {exc}") from exc
    stdout = cp.stdout.decode("utf-8", errors="replace")
    stderr = cp.stderr.decode("utf-8", errors="replace")
    if stderr.strip():
        logger(f"[router] stderr: {stderr.strip()}")
    if cp.returncode != 0:
        raise RuntimeError(f"Router prompt failed with rc={cp.returncode}")
    return stdout


def select_prompt(state: OrchestrationState, ctx: RouterContext, logger: Logger) -> PromptSelection:
    if not ctx.use_router:
        decision = select_prompt_with_mode(
            state,
            ctx.review_every_n_cycles,
            allowlist=ctx.allowlist,
            prompts_dir=ctx.prompts_dir,
            router_mode=ctx.router_mode,
            router_output=None,
        )
        prompt_path = resolve_prompt_path(decision.selected_prompt, ctx.prompts_dir)
        return PromptSelection(
            prompt_path=prompt_path,
            selected_prompt=decision.selected_prompt,
            decision=decision,
        )

    decision = select_prompt_with_mode(
        state,
        ctx.review_every_n_cycles,
        allowlist=ctx.allowlist,
        prompts_dir=ctx.prompts_dir,
        router_mode=ctx.router_mode,
        router_output=ctx.router_output,
    )
    log_router_decision(logger, state, decision)
    prompt_path = resolve_prompt_path(decision.selected_prompt, ctx.prompts_dir)
    return PromptSelection(
        prompt_path=prompt_path,
        selected_prompt=decision.selected_prompt,
        decision=decision,
    )


def run_turn(
    state: OrchestrationState,
    ctx: RouterContext,
    executor: PromptExecutor,
    logger: Logger,
) -> TurnResult:
    selection = select_prompt(state, ctx, logger)
    state.expected_step = selection.selected_prompt
    rc = executor(selection.prompt_path)
    if ctx.use_router:
        state.last_prompt = selection.selected_prompt
    return TurnResult(
        state=state,
        returncode=rc,
        prompt_path=selection.prompt_path,
        selected_prompt=selection.selected_prompt,
        decision=selection.decision,
    )
