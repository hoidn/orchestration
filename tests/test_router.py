from __future__ import annotations

from pathlib import Path

import pytest

from scripts.orchestration.config import load_config
from scripts.orchestration.router import (
    apply_router_override,
    deterministic_route,
    log_router_decision,
    select_prompt_with_mode,
)
from scripts.orchestration.state import OrchestrationState


def _write_prompt(path: Path) -> None:
    path.write_text("prompt", encoding="utf-8")


@pytest.mark.parametrize(
    "iteration,expected_actor,expected_prompt",
    [
        (1, "galph", "supervisor.md"),
        (1, "ralph", "main.md"),
        (2, "galph", "reviewer.md"),
    ],
)
def test_router_deterministic(tmp_path: Path, iteration: int, expected_actor: str, expected_prompt: str) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    prompt_map = {"galph": "supervisor.md", "ralph": "main.md", "reviewer": "reviewer.md"}
    allowlist = list(prompt_map.values())
    state = OrchestrationState(iteration=iteration, expected_actor=expected_actor, status="idle")

    decision = deterministic_route(
        state,
        prompt_map,
        review_every_n=2,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
    )

    assert decision.selected_prompt == expected_prompt
    assert decision.source == "deterministic"


def test_router_prompt_override(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    prompt_map = {"galph": "supervisor.md", "ralph": "main.md", "reviewer": "reviewer.md"}
    allowlist = list(prompt_map.values())
    state = OrchestrationState(iteration=3, expected_actor="ralph", status="idle")

    decision = apply_router_override(
        "reviewer.md\n",
        state,
        prompt_map,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
    )

    assert decision.selected_prompt == "reviewer.md"
    assert decision.source == "router"


def test_router_config_loads(tmp_path: Path) -> None:
    config_path = tmp_path / "orchestration.yaml"
    config_path.write_text(
        "\n".join(
            [
                "prompts_dir: prompts",
                "main_prompt: engineer.md",
                "reviewer_prompt: review.md",
                "router:",
                "  enabled: true",
                "  mode: router_only",
                "  prompt: router.md",
                "  review_every_n: 5",
                "  allowlist:",
                "    - supervisor.md",
                "    - engineer.md",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path=config_path, warn_missing=False)
    assert cfg.main_prompt == "engineer.md"
    assert cfg.reviewer_prompt == "review.md"
    assert cfg.router_enabled is True
    assert cfg.router_mode == "router_only"
    assert cfg.router_prompt == "router.md"
    assert cfg.router_review_every_n == 5
    assert cfg.router_allowlist == ["supervisor.md", "engineer.md"]


def test_router_logs_decision(tmp_path: Path) -> None:
    lines: list[str] = []
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)
    state = OrchestrationState(iteration=2, expected_actor="ralph", status="idle")
    decision = deterministic_route(
        state,
        {"galph": "supervisor.md", "ralph": "main.md", "reviewer": "reviewer.md"},
        review_every_n=0,
        allowlist=["supervisor.md", "main.md", "reviewer.md"],
        prompts_dir=prompts_dir,
    )

    log_router_decision(lines.append, state, decision)

    assert lines
    assert "actor=ralph" in lines[0]
    assert "prompt=main.md" in lines[0]


def test_router_mode_selection(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    prompt_map = {"galph": "supervisor.md", "ralph": "main.md", "reviewer": "reviewer.md"}
    allowlist = list(prompt_map.values())
    state = OrchestrationState(iteration=1, expected_actor="ralph", status="idle")

    decision = select_prompt_with_mode(
        state,
        prompt_map,
        review_every_n=2,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
        router_mode="router_first",
        router_output=None,
    )
    assert decision.selected_prompt == "main.md"

    decision = select_prompt_with_mode(
        state,
        prompt_map,
        review_every_n=2,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
        router_mode="router_first",
        router_output="reviewer.md",
    )
    assert decision.selected_prompt == "reviewer.md"


def test_router_only_enforces_actor(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    prompt_map = {"galph": "supervisor.md", "ralph": "main.md", "reviewer": "reviewer.md"}
    allowlist = list(prompt_map.values())
    state = OrchestrationState(iteration=1, expected_actor="ralph", status="idle")

    with pytest.raises(ValueError):
        select_prompt_with_mode(
            state,
            prompt_map,
            review_every_n=2,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
            router_mode="router_only",
            router_output="supervisor.md",
        )
