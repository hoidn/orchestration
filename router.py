from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .config import load_config
from .state import OrchestrationState


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


def _ensure_actor_allowed(candidate: str, expected_actor: str, prompt_map: Mapping[str, str]) -> None:
    candidate_norm = _normalize_prompt_token(candidate)
    expected_norm = _normalize_prompt_token(prompt_map[expected_actor])
    reviewer = prompt_map.get("reviewer")
    reviewer_norm = _normalize_prompt_token(reviewer) if reviewer else None
    if candidate_norm == expected_norm:
        return
    if reviewer_norm and candidate_norm == reviewer_norm:
        return
    raise ValueError(
        f"Prompt '{candidate_norm}' is not allowed for expected_actor='{expected_actor}'. "
        f"Expected '{expected_norm}' or reviewer='{reviewer_norm}'."
    )


def deterministic_route(
    state: OrchestrationState,
    prompt_map: Mapping[str, str],
    review_every_n: int,
    *,
    allowlist: Optional[Iterable[str]] = None,
    prompts_dir: Path = Path("prompts"),
) -> RouterDecision:
    expected_actor = state.expected_actor
    if expected_actor not in prompt_map:
        raise ValueError(
            f"Unknown expected_actor '{expected_actor}'. "
            f"Valid actors: {sorted(prompt_map.keys())}"
        )

    candidate = prompt_map[expected_actor]
    reason = f"expected_actor={expected_actor}"
    if review_every_n > 0 and state.iteration % review_every_n == 0:
        reviewer = prompt_map.get("reviewer")
        if not reviewer:
            raise ValueError("review_every_n set but prompt_map missing 'reviewer' entry.")
        reviewer_norm = _normalize_prompt_token(reviewer)
        last_prompt_norm = _normalize_prompt_token(state.last_prompt) if state.last_prompt else None
        if (
            expected_actor == "ralph"
            and last_prompt_norm == reviewer_norm
            and state.last_prompt_actor == "galph"
        ):
            reason = (
                "review cadence skipped for ralph; "
                "reviewer already ran on galph turn"
            )
        else:
            candidate = reviewer
            reason = f"review cadence hit (iteration={state.iteration}, every={review_every_n})"

    allow_tokens = allowlist if allowlist is not None else prompt_map.values()
    allowset = _normalize_allowlist(allow_tokens)
    candidate_norm = _normalize_prompt_token(candidate)
    if candidate_norm not in allowset:
        raise ValueError(f"Prompt '{candidate_norm}' not in allowlist: {sorted(allowset)}")

    _ensure_actor_allowed(candidate, expected_actor, prompt_map)

    prompt_path = resolve_prompt_path(candidate, prompts_dir)
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found for '{candidate_norm}': {prompt_path}")

    return RouterDecision(selected_prompt=candidate_norm, source="deterministic", reason=reason)


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
    prompt_map: Mapping[str, str],
    *,
    allowlist: Optional[Iterable[str]] = None,
    prompts_dir: Path = Path("prompts"),
) -> RouterDecision:
    override = parse_router_output(raw_output)
    allow_tokens = allowlist if allowlist is not None else prompt_map.values()
    allowset = _normalize_allowlist(allow_tokens)
    override_norm = _normalize_prompt_token(override)
    if override_norm not in allowset:
        raise ValueError(f"Router override '{override_norm}' not in allowlist: {sorted(allowset)}")

    _ensure_actor_allowed(override, state.expected_actor, prompt_map)

    prompt_path = resolve_prompt_path(override, prompts_dir)
    if not prompt_path.exists():
        raise FileNotFoundError(f"Router override prompt not found: {prompt_path}")

    return RouterDecision(selected_prompt=override_norm, source="router", reason="router override")


def select_prompt_with_mode(
    state: OrchestrationState,
    prompt_map: Mapping[str, str],
    review_every_n: int,
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
            prompt_map,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )

    if mode == "router_first":
        if router_output:
            return apply_router_override(
                router_output,
                state,
                prompt_map,
                allowlist=allowlist,
                prompts_dir=prompts_dir,
            )
        return deterministic_route(
            state,
            prompt_map,
            review_every_n,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )

    decision = deterministic_route(
        state,
        prompt_map,
        review_every_n,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
    )
    if router_output:
        decision = apply_router_override(
            router_output,
            state,
            prompt_map,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )
    return decision


def route_from_state_file(
    state_file: Path,
    prompt_map: Mapping[str, str],
    review_every_n: int,
    *,
    allowlist: Optional[Iterable[str]] = None,
    prompts_dir: Path = Path("prompts"),
) -> RouterDecision:
    state = OrchestrationState.read(str(state_file))
    return deterministic_route(
        state,
        prompt_map,
        review_every_n,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
    )


def log_router_decision(logger, state: OrchestrationState, decision: RouterDecision) -> None:
    logger(
        "[router] iteration=%s actor=%s prompt=%s source=%s reason=%s"
        % (
            state.iteration,
            state.expected_actor,
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

    prompt_map: dict[str, str] = {
        "galph": args.prompt_supervisor,
        "ralph": args.prompt_main,
    }
    if args.prompt_reviewer:
        prompt_map["reviewer"] = args.prompt_reviewer

    allowlist = [item.strip() for item in args.allowlist.split(",") if item.strip()] or None

    try:
        decision = route_from_state_file(
            args.state_file,
            prompt_map,
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
