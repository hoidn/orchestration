"""Regression tests for sync supervisor/loop router review cadence.

This module verifies that the reviewer prompt runs exactly once per iteration
when review cadence is triggered, by checking that `last_prompt_actor` is
persisted correctly so ralph can skip the duplicate reviewer selection.

Reference: scripts/orchestration/README.md:130 (authoritative cadence behavior)
Reference: scripts/orchestration/router.py:88-112 (deterministic routing skip guard)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.orchestration.router import deterministic_route
from scripts.orchestration.state import OrchestrationState


def _write_prompt(path: Path) -> None:
    path.write_text("prompt", encoding="utf-8")


class TestSyncRouterReview:
    """Test suite for sync router review cadence."""

    def test_review_runs_once(self, tmp_path: Path) -> None:
        """Verify reviewer runs only once per iteration when cadence hits.

        Simulates a review cadence hit on galph's turn, then verifies ralph
        skips the reviewer because `last_prompt_actor` is set.

        Without the state annotations (last_prompt_actor), this test would fail
        because ralph would also select reviewer on the cadence iteration.
        """
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        for name in ("supervisor.md", "main.md", "reviewer.md"):
            _write_prompt(prompts_dir / name)

        prompt_map = {"galph": "supervisor.md", "ralph": "main.md", "reviewer": "reviewer.md"}
        allowlist = list(prompt_map.values())

        # Iteration 2 with review_every_n=2 triggers cadence
        state = OrchestrationState(
            iteration=2,
            expected_actor="galph",
            status="idle",
        )

        # galph turn: should select reviewer due to cadence
        galph_decision = deterministic_route(
            state,
            prompt_map,
            review_every_n=2,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )
        assert galph_decision.selected_prompt == "reviewer.md"
        assert "review cadence hit" in galph_decision.reason

        # Simulate galph writing state with last_prompt_actor after prompt selection
        state.last_prompt = galph_decision.selected_prompt
        state.last_prompt_actor = "galph"
        state.expected_actor = "ralph"
        state.status = "waiting-ralph"

        # ralph turn: should skip reviewer since galph already ran it
        ralph_decision = deterministic_route(
            state,
            prompt_map,
            review_every_n=2,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )
        assert ralph_decision.selected_prompt == "main.md"
        assert "review cadence skipped" in ralph_decision.reason
        assert "reviewer already ran on galph turn" in ralph_decision.reason

    def test_review_cadence_without_actor_annotation_fails(self, tmp_path: Path) -> None:
        """Prove that without last_prompt_actor, reviewer would run twice.

        This test documents the bug that existed before the fix: when
        last_prompt_actor was not set, ralph would also select reviewer
        on the cadence iteration, causing duplicate reviewer runs.
        """
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        for name in ("supervisor.md", "main.md", "reviewer.md"):
            _write_prompt(prompts_dir / name)

        prompt_map = {"galph": "supervisor.md", "ralph": "main.md", "reviewer": "reviewer.md"}
        allowlist = list(prompt_map.values())

        # Iteration 2 with review_every_n=2 triggers cadence
        state = OrchestrationState(
            iteration=2,
            expected_actor="galph",
            status="idle",
        )

        # galph turn: should select reviewer due to cadence
        galph_decision = deterministic_route(
            state,
            prompt_map,
            review_every_n=2,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )
        assert galph_decision.selected_prompt == "reviewer.md"

        # Simulate galph writing state WITHOUT last_prompt_actor (the bug case)
        state.last_prompt = galph_decision.selected_prompt
        # NOTE: last_prompt_actor is NOT set - this was the missing annotation
        state.last_prompt_actor = None  # Explicitly showing the missing annotation
        state.expected_actor = "ralph"
        state.status = "waiting-ralph"

        # ralph turn: without the actor annotation, reviewer runs again (bug!)
        ralph_decision = deterministic_route(
            state,
            prompt_map,
            review_every_n=2,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )
        # This demonstrates the bug: reviewer selected twice
        assert ralph_decision.selected_prompt == "reviewer.md"
        assert "review cadence hit" in ralph_decision.reason

    def test_last_prompt_actor_toggles_correctly(self, tmp_path: Path) -> None:
        """Verify last_prompt_actor correctly toggles between galph and ralph.

        This test simulates multiple iterations to ensure the state annotation
        is correctly maintained across the galph→ralph→galph cycle.
        """
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        for name in ("supervisor.md", "main.md", "reviewer.md"):
            _write_prompt(prompts_dir / name)

        prompt_map = {"galph": "supervisor.md", "ralph": "main.md", "reviewer": "reviewer.md"}
        allowlist = list(prompt_map.values())

        # Start at iteration 1 (non-cadence)
        state = OrchestrationState(
            iteration=1,
            expected_actor="galph",
            status="idle",
        )

        # Iteration 1: galph turn
        galph_decision_1 = deterministic_route(
            state,
            prompt_map,
            review_every_n=2,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )
        assert galph_decision_1.selected_prompt == "supervisor.md"

        # Simulate state update after galph turn
        state.last_prompt = galph_decision_1.selected_prompt
        state.last_prompt_actor = "galph"
        state.expected_actor = "ralph"

        # Iteration 1: ralph turn
        ralph_decision_1 = deterministic_route(
            state,
            prompt_map,
            review_every_n=2,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )
        assert ralph_decision_1.selected_prompt == "main.md"

        # Simulate state update after ralph turn
        state.last_prompt = ralph_decision_1.selected_prompt
        state.last_prompt_actor = "ralph"
        state.expected_actor = "galph"
        state.iteration = 2  # Increment for next iteration

        # Iteration 2: galph turn (cadence hit)
        galph_decision_2 = deterministic_route(
            state,
            prompt_map,
            review_every_n=2,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )
        assert galph_decision_2.selected_prompt == "reviewer.md"

        # Simulate state update after galph turn
        state.last_prompt = galph_decision_2.selected_prompt
        state.last_prompt_actor = "galph"
        state.expected_actor = "ralph"

        # Iteration 2: ralph turn (should skip reviewer)
        ralph_decision_2 = deterministic_route(
            state,
            prompt_map,
            review_every_n=2,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )
        assert ralph_decision_2.selected_prompt == "main.md"
        assert "review cadence skipped" in ralph_decision_2.reason

    def test_non_cadence_iteration_no_reviewer(self, tmp_path: Path) -> None:
        """Verify non-cadence iterations don't trigger reviewer selection."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        for name in ("supervisor.md", "main.md", "reviewer.md"):
            _write_prompt(prompts_dir / name)

        prompt_map = {"galph": "supervisor.md", "ralph": "main.md", "reviewer": "reviewer.md"}
        allowlist = list(prompt_map.values())

        # Iteration 3 with review_every_n=2 does NOT trigger cadence
        state = OrchestrationState(
            iteration=3,
            expected_actor="galph",
            status="idle",
        )

        galph_decision = deterministic_route(
            state,
            prompt_map,
            review_every_n=2,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )
        assert galph_decision.selected_prompt == "supervisor.md"
        assert "expected_actor=galph" in galph_decision.reason

        state.last_prompt = galph_decision.selected_prompt
        state.last_prompt_actor = "galph"
        state.expected_actor = "ralph"

        ralph_decision = deterministic_route(
            state,
            prompt_map,
            review_every_n=2,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
        )
        assert ralph_decision.selected_prompt == "main.md"
        assert "expected_actor=ralph" in ralph_decision.reason
