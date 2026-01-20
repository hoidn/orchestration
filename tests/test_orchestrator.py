from __future__ import annotations

import os
import sys
from pathlib import Path

from scripts.orchestration import orchestrator as orchestrator_module
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


def test_router_override_galph_only(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    state = OrchestrationState(iteration=3, expected_actor="galph", status="idle")
    allowlist = ["supervisor.md", "main.md", "reviewer.md"]

    galph_ctx, ralph_ctx = build_combined_contexts(
        prompts_dir=prompts_dir,
        supervisor_prompt="supervisor.md",
        main_prompt="main.md",
        reviewer_prompt="reviewer.md",
        allowlist=allowlist,
        review_every_n=0,
        router_mode="router_default",
        router_output="reviewer.md",
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


def test_router_disabled_uses_actor_prompts(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    state = OrchestrationState(iteration=2, expected_actor="galph", status="idle")
    allowlist = ["supervisor.md", "main.md", "reviewer.md"]

    galph_ctx, ralph_ctx = build_combined_contexts(
        prompts_dir=prompts_dir,
        supervisor_prompt="supervisor.md",
        main_prompt="main.md",
        reviewer_prompt="reviewer.md",
        allowlist=allowlist,
        review_every_n=1,
        router_mode="router_default",
        router_output="reviewer.md",
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


def test_combined_missing_prompt_marks_failed(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("main.md", "reviewer.md"):
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

    errors: list[str] = []

    def _log(msg: str) -> None:
        errors.append(msg)

    rc = run_combined_iteration(
        state=state,
        galph_ctx=galph_ctx,
        ralph_ctx=ralph_ctx,
        galph_executor=lambda _: 0,
        ralph_executor=lambda _: 0,
        galph_logger=_log,
        ralph_logger=_log,
        state_writer=lambda _: None,
    )

    assert rc == 2
    assert state.status == "failed"
    assert state.expected_actor == "galph"
    assert state.iteration == 1
    assert any("galph turn failed" in msg for msg in errors)


def test_router_only_without_output_marks_failed(tmp_path: Path) -> None:
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
        router_mode="router_only",
        router_output=None,
        use_router=True,
    )

    errors: list[str] = []

    def _log(msg: str) -> None:
        errors.append(msg)

    rc = run_combined_iteration(
        state=state,
        galph_ctx=galph_ctx,
        ralph_ctx=ralph_ctx,
        galph_executor=lambda _: 0,
        ralph_executor=lambda _: 0,
        galph_logger=_log,
        ralph_logger=_log,
        state_writer=lambda _: None,
    )

    assert rc == 2
    assert state.status == "failed"
    assert state.expected_actor == "galph"
    assert state.iteration == 1
    assert any("router_only" in msg for msg in errors)


def test_role_mode_requires_role(monkeypatch) -> None:
    called = {"supervisor": False, "loop": False}

    def _supervisor() -> int:
        called["supervisor"] = True
        return 0

    def _loop() -> int:
        called["loop"] = True
        return 0

    monkeypatch.setattr(orchestrator_module, "supervisor_main", _supervisor)
    monkeypatch.setattr(orchestrator_module, "loop_main", _loop)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "--mode", "role", "--sync-via-git"])

    rc = orchestrator_module.main()

    assert rc == 2
    assert called == {"supervisor": False, "loop": False}


def test_role_mode_requires_sync(monkeypatch) -> None:
    called = {"supervisor": False, "loop": False}

    def _supervisor() -> int:
        called["supervisor"] = True
        return 0

    def _loop() -> int:
        called["loop"] = True
        return 0

    monkeypatch.setattr(orchestrator_module, "supervisor_main", _supervisor)
    monkeypatch.setattr(orchestrator_module, "loop_main", _loop)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "--mode", "role", "--role", "galph"])

    rc = orchestrator_module.main()

    assert rc == 2
    assert called == {"supervisor": False, "loop": False}


def test_role_mode_forwards_supervisor_prompt(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    def _supervisor() -> int:
        captured["value"] = os.getenv("SUPERVISOR_PROMPT")
        return 7

    monkeypatch.delenv("SUPERVISOR_PROMPT", raising=False)
    monkeypatch.setattr(orchestrator_module, "supervisor_main", _supervisor)
    monkeypatch.setattr(orchestrator_module, "loop_main", lambda: 0)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "orchestrator",
            "--mode",
            "role",
            "--role",
            "galph",
            "--sync-via-git",
            "--prompt-supervisor",
            "custom_supervisor.md",
        ],
    )

    rc = orchestrator_module.main()

    assert rc == 7
    assert captured["value"] == "custom_supervisor.md"


def test_role_mode_forwards_loop_prompt(monkeypatch) -> None:
    captured: dict[str, str | None] = {}
    called = {"supervisor": False}

    def _loop() -> int:
        captured["value"] = os.getenv("LOOP_PROMPT")
        return 8

    def _supervisor() -> int:
        called["supervisor"] = True
        return 0

    monkeypatch.delenv("LOOP_PROMPT", raising=False)
    monkeypatch.setattr(orchestrator_module, "loop_main", _loop)
    monkeypatch.setattr(orchestrator_module, "supervisor_main", _supervisor)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "orchestrator",
            "--mode",
            "role",
            "--role",
            "ralph",
            "--sync-via-git",
            "--prompt-main",
            "custom_main.md",
        ],
    )

    rc = orchestrator_module.main()

    assert rc == 8
    assert captured["value"] == "custom_main.md"
    assert called["supervisor"] is False
