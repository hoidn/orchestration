from __future__ import annotations

from pathlib import Path

from scripts.orchestration.orchestrator import build_combined_contexts, run_combined_iteration
from scripts.orchestration.state import OrchestrationState


def _write_prompt(path: Path) -> None:
    path.write_text("prompt", encoding="utf-8")


def test_combined_sequence(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    state = OrchestrationState(iteration=1, expected_actor="galph", status="idle")
    allowlist = ["supervisor.md", "main.md", "reviewer.md"]

    galph_ctx, ralph_ctx = build_combined_contexts(
        prompts_dir=prompts_dir,
        supervisor_prompt="supervisor.md",
        main_prompt="main.md",
        reviewer_prompt="reviewer.md",
        allowlist=allowlist,
        review_every_n=0,
        router_mode="router_default",
        router_output=None,
        use_router=False,
    )

    executed: list[str] = []

    def _exec(prompt_path: Path) -> int:
        executed.append(prompt_path.name)
        return 0

    rc = run_combined_iteration(
        state=state,
        galph_ctx=galph_ctx,
        ralph_ctx=ralph_ctx,
        galph_executor=_exec,
        ralph_executor=_exec,
        galph_logger=lambda _: None,
        ralph_logger=lambda _: None,
        state_writer=lambda _: None,
    )

    assert rc == 0
    assert executed == ["supervisor.md", "main.md"]
    assert state.iteration == 2
    assert state.expected_actor == "galph"
    assert state.status == "complete"


def test_review_cadence_single(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    state = OrchestrationState(iteration=1, expected_actor="galph", status="idle")
    allowlist = ["supervisor.md", "main.md", "reviewer.md"]

    galph_ctx, ralph_ctx = build_combined_contexts(
        prompts_dir=prompts_dir,
        supervisor_prompt="supervisor.md",
        main_prompt="main.md",
        reviewer_prompt="reviewer.md",
        allowlist=allowlist,
        review_every_n=1,
        router_mode="router_default",
        router_output=None,
        use_router=True,
    )

    executed: list[str] = []

    def _exec(prompt_path: Path) -> int:
        executed.append(prompt_path.name)
        return 0

    rc = run_combined_iteration(
        state=state,
        galph_ctx=galph_ctx,
        ralph_ctx=ralph_ctx,
        galph_executor=_exec,
        ralph_executor=_exec,
        galph_logger=lambda _: None,
        ralph_logger=lambda _: None,
        state_writer=lambda _: None,
    )

    assert rc == 0
    assert executed == ["reviewer.md", "main.md"]
    assert state.last_prompt == "main.md"
