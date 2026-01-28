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
    "step_index,expected_prompt",
    [
        (0, "supervisor.md"),
        (1, "main.md"),
        (2, "supervisor.md"),
    ],
)
def test_router_deterministic_standard(tmp_path: Path, step_index: int, expected_prompt: str) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    allowlist = ["supervisor.md", "main.md", "reviewer.md"]
    state = OrchestrationState(
        workflow_name="standard",
        step_index=step_index,
        iteration=step_index + 1,
        status="idle",
    )

    decision = deterministic_route(
        state,
        review_every_n_cycles=0,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
    )

    assert decision.selected_prompt == expected_prompt
    assert decision.source == "workflow"


def test_router_deterministic_review_cadence(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    allowlist = ["supervisor.md", "main.md", "reviewer.md"]
    state = OrchestrationState(
        workflow_name="review_cadence",
        step_index=2,
        iteration=3,
        status="idle",
    )

    decision = deterministic_route(
        state,
        review_every_n_cycles=2,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
    )

    assert decision.selected_prompt == "reviewer.md"
    assert decision.source == "workflow"


def test_router_prompt_override(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    allowlist = ["supervisor.md", "main.md", "reviewer.md"]
    state = OrchestrationState(workflow_name="standard", step_index=1, iteration=2, status="idle")

    decision = apply_router_override(
        "reviewer.md\n",
        state,
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
                "workflow:",
                "  name: review_cadence",
                "  review_every_n: 3",
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
    assert cfg.workflow_name == "review_cadence"
    assert cfg.workflow_review_every_n == 3
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
    state = OrchestrationState(workflow_name="standard", step_index=1, iteration=2, status="idle")
    decision = deterministic_route(
        state,
        review_every_n_cycles=0,
        allowlist=["supervisor.md", "main.md", "reviewer.md"],
        prompts_dir=prompts_dir,
    )

    log_router_decision(lines.append, state, decision)

    assert lines
    assert "workflow=standard" in lines[0]
    assert "step=1" in lines[0]
    assert "prompt=main.md" in lines[0]


def test_router_mode_selection(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    allowlist = ["supervisor.md", "main.md", "reviewer.md"]
    state = OrchestrationState(workflow_name="standard", step_index=1, iteration=2, status="idle")

    decision = select_prompt_with_mode(
        state,
        review_every_n_cycles=0,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
        router_mode="router_first",
        router_output=None,
    )
    assert decision.selected_prompt == "main.md"

    decision = select_prompt_with_mode(
        state,
        review_every_n_cycles=0,
        allowlist=allowlist,
        prompts_dir=prompts_dir,
        router_mode="router_first",
        router_output="reviewer.md",
    )
    assert decision.selected_prompt == "reviewer.md"


def test_router_only_enforces_allowlist(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    allowlist = ["main.md"]
    state = OrchestrationState(workflow_name="standard", step_index=0, iteration=1, status="idle")

    with pytest.raises(ValueError):
        select_prompt_with_mode(
            state,
            review_every_n_cycles=0,
            allowlist=allowlist,
            prompts_dir=prompts_dir,
            router_mode="router_only",
            router_output="reviewer.md",
        )


def test_spec_bootstrap_defaults(tmp_path: Path) -> None:
    """Test that SpecBootstrapConfig defaults to specs/ and exercises legacy template fallback."""
    from scripts.orchestration.config import SpecBootstrapConfig

    # Test 1: Minimal orchestration.yaml with spec_bootstrap block lacking specs.dir
    config_path = tmp_path / "orchestration.yaml"
    config_path.write_text(
        "\n".join(
            [
                "prompts_dir: prompts",
                "spec_bootstrap:",
                "  templates_dir: " + str(tmp_path / "templates"),
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path=config_path, warn_missing=False)
    assert cfg.spec_bootstrap is not None
    assert cfg.spec_bootstrap.specs_dir == Path("specs"), "Default specs_dir should be specs/"
