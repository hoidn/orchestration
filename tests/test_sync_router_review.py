"""Regression tests for workflow review cadence.

Ensures review cadence replaces both steps in the review cycle when using
workflow-based routing.
"""
from __future__ import annotations

from pathlib import Path

from scripts.orchestration.router import deterministic_route
from scripts.orchestration.state import OrchestrationState


def _write_prompt(path: Path) -> None:
    path.write_text("prompt", encoding="utf-8")


def _setup_prompts(tmp_path: Path) -> Path:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)
    return prompts_dir


def test_review_cadence_replaces_both_steps(tmp_path: Path) -> None:
    prompts_dir = _setup_prompts(tmp_path)
    allowlist = ["supervisor.md", "main.md", "reviewer.md"]

    state = OrchestrationState(workflow_name="review_cadence", step_index=2, iteration=3, status="idle")
    decision = deterministic_route(
        state,
        review_every_n_cycles=2,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
    )
    assert decision.selected_prompt == "reviewer.md"

    state = OrchestrationState(workflow_name="review_cadence", step_index=3, iteration=4, status="idle")
    decision = deterministic_route(
        state,
        review_every_n_cycles=2,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
    )
    assert decision.selected_prompt == "reviewer.md"


def test_non_review_cycle_uses_base_steps(tmp_path: Path) -> None:
    prompts_dir = _setup_prompts(tmp_path)
    allowlist = ["supervisor.md", "main.md", "reviewer.md"]

    state = OrchestrationState(workflow_name="review_cadence", step_index=0, iteration=1, status="idle")
    decision = deterministic_route(
        state,
        review_every_n_cycles=2,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
    )
    assert decision.selected_prompt == "supervisor.md"

    state = OrchestrationState(workflow_name="review_cadence", step_index=1, iteration=2, status="idle")
    decision = deterministic_route(
        state,
        review_every_n_cycles=2,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
    )
    assert decision.selected_prompt == "main.md"
