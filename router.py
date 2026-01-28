from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .config import load_config
from .state import OrchestrationState
from .workflows import get_workflow, resolve_step


@dataclass(frozen=True)
class RouterDecision:
    selected_prompt: str
    source: str
    reason: str


def normalize_router_mode(value: str | None) -> str:
    if not value:
        return "router_default"
    normalized = value.strip().lower()
    aliases = {
        "default": "router_default",
        "router": "router_default",
        "router_default": "router_default",
        "router-first": "router_first",
        "router_first": "router_first",
        "first": "router_first",
        "router-only": "router_only",
        "router_only": "router_only",
        "only": "router_only",
    }
    if normalized in aliases:
        return aliases[normalized]
    raise ValueError(f"Unsupported router mode '{value}'. Use router_default, router_first, or router_only.")


def _normalize_prompt_token(token: str) -> str:
    path = Path(token)
    if path.suffix != ".md":
        path = path.with_suffix(".md")
    return path.as_posix()


def _normalize_allowlist(allowlist: Iterable[str]) -> set[str]:
    return {_normalize_prompt_token(item) for item in allowlist}


def resolve_prompt_path(token: str, prompts_dir: Path) -> Path:
    normalized = _normalize_prompt_token(token)
    path = Path(normalized)
    if path.is_absolute():
        return path
    if path.parts and prompts_dir.name and path.parts[0] == prompts_dir.name:
        return prompts_dir.parent / path
    return prompts_dir / path


def deterministic_route(
    state: OrchestrationState,
    review_every_n_cycles: int,
    *,
    allowlist: Optional[Iterable[str]] = None,
    prompts_dir: Path = Path("prompts"),
) -> RouterDecision:
    workflow = get_workflow(state.workflow_name, review_every_n_cycles=review_every_n_cycles)
    step = resolve_step(workflow, step_index=state.step_index)
    candidate = step.prompt
    reason = f"workflow={workflow.name} step={state.step_index}"

    allow_tokens = allowlist if allowlist is not None else [candidate]
    allowset = _normalize_allowlist(allow_tokens)
    candidate_norm = _normalize_prompt_token(candidate)
    if candidate_norm not in allowset:
        raise ValueError(f"Prompt '{candidate_norm}' not in allowlist: {sorted(allowset)}")

    prompt_path = resolve_prompt_path(candidate, prompts_dir)
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found for '{candidate_norm}': {prompt_path}")

    return RouterDecision(selected_prompt=candidate_norm, source="workflow", reason=reason)


def parse_router_output(raw_output: str) -> str:
    lines = [line.strip() for line in raw_output.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Router output is empty; expected a single prompt name/path.")
    if len(lines) > 1:
        raise ValueError("Router output must be a single non-empty line.")
    return lines[0]


def apply_router_override(
    raw_output: str,
    state: OrchestrationState,
    *,
    allowlist: Optional[Iterable[str]] = None,
    prompts_dir: Path = Path("prompts"),
) -> RouterDecision:
    override = parse_router_output(raw_output)
    allow_tokens = allowlist if allowlist is not None else [override]
    allowset = _normalize_allowlist(allow_tokens)
    override_norm = _normalize_prompt_token(override)
    if override_norm not in allowset:
        raise ValueError(f"Router override '{override_norm}' not in allowlist: {sorted(allowset)}")

    prompt_path = resolve_prompt_path(override, prompts_dir)
    if not prompt_path.exists():
        raise FileNotFoundError(f"Router override prompt not found: {prompt_path}")

    return RouterDecision(selected_prompt=override_norm, source="router", reason="router override")


def select_prompt_with_mode(
    state: OrchestrationState,
    review_every_n_cycles: int,
    *,
    allowlist: Optional[Iterable[str]] = None,
    prompts_dir: Path = Path("prompts"),
    router_mode: str | None = None,
    router_output: Optional[str] = None,
) -> RouterDecision:
    mode = normalize_router_mode(router_mode)
    if mode == "router_only":
        if not router_output:
            raise ValueError("router_only mode requires router prompt output.")
        return apply_router_override(
            router_output,
            state,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )

    if mode == "router_first":
        if router_output:
            return apply_router_override(
                router_output,
                state,
                allowlist=allowlist,
                prompts_dir=prompts_dir,
            )
        return deterministic_route(
            state,
            review_every_n_cycles,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )

    decision = deterministic_route(
        state,
        review_every_n_cycles,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
    )
    if router_output:
        decision = apply_router_override(
            router_output,
            state,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )
    return decision


def route_from_state_file(
    state_file: Path,
    review_every_n_cycles: int,
    *,
    allowlist: Optional[Iterable[str]] = None,
    prompts_dir: Path = Path("prompts"),
) -> RouterDecision:
    state = OrchestrationState.read(str(state_file))
    return deterministic_route(
        state,
        review_every_n_cycles,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
    )


def log_router_decision(logger, state: OrchestrationState, decision: RouterDecision) -> None:
    logger(
        "[router] iteration=%s workflow=%s step=%s prompt=%s source=%s reason=%s"
        % (
            state.iteration,
            state.workflow_name,
            state.step_index,
            decision.selected_prompt,
            decision.source,
            decision.reason,
        )
    )


def main() -> int:
    cfg = load_config(warn_missing=False)

    ap = argparse.ArgumentParser(description="Deterministic router for orchestration prompts.")
    ap.add_argument("--state-file", type=Path, default=Path(os.getenv("STATE_FILE", str(cfg.state_file))))
    ap.add_argument("--prompts-dir", type=Path, default=Path(os.getenv("PROMPTS_DIR", str(cfg.prompts_dir))))
    ap.add_argument(
        "--review-every-n",
        type=int,
        default=int(os.getenv("ROUTER_REVIEW_EVERY_N", "0")),
        help="Route to reviewer prompt every N iterations (0 disables).",
    )
    ap.add_argument("--prompt-supervisor", type=str, default=os.getenv("ROUTER_PROMPT_SUPERVISOR", "supervisor.md"))
    ap.add_argument("--prompt-main", type=str, default=os.getenv("ROUTER_PROMPT_MAIN", "main.md"))
    ap.add_argument("--prompt-reviewer", type=str, default=os.getenv("ROUTER_PROMPT_REVIEWER", "reviewer.md"))
    ap.add_argument(
        "--allowlist",
        type=str,
        default=os.getenv("ROUTER_ALLOWLIST", ""),
        help="Comma-separated allowlist of prompt names/paths. Defaults to prompt_map values.",
    )
    ap.add_argument("--print-reason", action="store_true", help="Print routing rationale to stderr.")
    args = ap.parse_args()

    allowlist = [item.strip() for item in args.allowlist.split(",") if item.strip()] or None

    try:
        decision = route_from_state_file(
            args.state_file,
            args.review_every_n,
            allowlist=allowlist,
            prompts_dir=args.prompts_dir,
        )
    except Exception as exc:
        print(f"[router] ERROR: {exc}", file=sys.stderr)
        return 2

    if args.print_reason:
        print(f"[router] {decision.source}: {decision.reason}", file=sys.stderr)

    print(decision.selected_prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
