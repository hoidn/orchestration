from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from subprocess import PIPE, Popen, run
from typing import Callable, Iterable, Mapping, Optional

from .router import RouterDecision, log_router_decision, resolve_prompt_path, select_prompt_with_mode
from .state import OrchestrationState


PromptExecutor = Callable[[Path], int]
Logger = Callable[[str], None]


@dataclass(frozen=True)
class RouterContext:
    prompts_dir: Path
    prompt_map: Mapping[str, str]
    allowlist: Optional[Iterable[str]]
    review_every_n: int
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
    import select

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as flog:
        flog.write(f"$ {' '.join(cmd)}\n")
        flog.flush()

        fin = open(stdin_file, "rb") if stdin_file else open("/dev/null", "rb")
        try:
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
                        text = chunk.decode("utf-8", errors="replace")
                        sys.stdout.write(text)
                        sys.stdout.flush()
                        flog.write(text)
                        flog.flush()

                if stderr_fd in readable and proc.stderr:
                    chunk = proc.stderr.read(4096)
                    if chunk:
                        text = chunk.decode("utf-8", errors="replace")
                        sys.stderr.write(text)
                        sys.stderr.flush()
                        flog.write(text)
                        flog.flush()

                if proc.poll() is not None:
                    if proc.stdout:
                        remaining = proc.stdout.read()
                        if remaining:
                            text = remaining.decode("utf-8", errors="replace")
                            sys.stdout.write(text)
                            sys.stdout.flush()
                            flog.write(text)
                    if proc.stderr:
                        remaining = proc.stderr.read()
                        if remaining:
                            text = remaining.decode("utf-8", errors="replace")
                            sys.stderr.write(text)
                            sys.stderr.flush()
                            flog.write(text)
                    break

            return proc.returncode
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
        prompt_name = ctx.prompt_map[state.expected_actor]
        if not prompt_name.endswith(".md"):
            prompt_name = f"{prompt_name}.md"
        prompt_path = resolve_prompt_path(prompt_name, ctx.prompts_dir)
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
        return PromptSelection(prompt_path=prompt_path, selected_prompt=prompt_name)

    decision = select_prompt_with_mode(
        state,
        ctx.prompt_map,
        ctx.review_every_n,
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
    role: str,
    state: OrchestrationState,
    ctx: RouterContext,
    executor: PromptExecutor,
    logger: Logger,
) -> TurnResult:
    state.expected_actor = role
    selection = select_prompt(state, ctx, logger)
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
