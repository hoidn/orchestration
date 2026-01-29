from __future__ import annotations

import os
import sys
from pathlib import Path

import argparse

from scripts.orchestration import orchestrator as orchestrator_module
from scripts.orchestration.orchestrator import (
    CombinedAutoCommitConfig,
    build_combined_contexts,
    run_combined_autocommit,
    run_combined_iteration,
)
from scripts.orchestration.config import OrchConfig
from scripts.orchestration.runner import resolve_use_pty
from scripts.orchestration.state import OrchestrationState


def _write_prompt(path: Path) -> None:
    path.write_text("prompt", encoding="utf-8")


def _build_contexts(
    *,
    prompts_dir: Path,
    allowlist: list[str],
    review_every_n_cycles: int = 0,
    router_mode: str = "router_default",
    router_output: str | None = None,
    use_router: bool = False,
) -> tuple:
    return build_combined_contexts(
        prompts_dir=prompts_dir,
        allowlist=allowlist,
        review_every_n_cycles=review_every_n_cycles,
        router_mode=router_mode,
        router_output=router_output,
        use_router=use_router,
    )


def test_combined_sequence(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    state = OrchestrationState(workflow_name="standard", step_index=0, iteration=1, status="idle")
    allowlist = ["supervisor.md", "main.md", "reviewer.md"]

    galph_ctx, ralph_ctx = _build_contexts(
        prompts_dir=prompts_dir,
        allowlist=allowlist,
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
    assert state.step_index == 2
    assert state.iteration == 3
    assert state.expected_step == "main.md"
    assert state.status == "complete"


def test_review_cadence_single(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    state = OrchestrationState(workflow_name="review_cadence", step_index=0, iteration=1, status="idle")
    allowlist = ["supervisor.md", "main.md", "reviewer.md"]

    galph_ctx, ralph_ctx = _build_contexts(
        prompts_dir=prompts_dir,
        allowlist=allowlist,
        review_every_n_cycles=1,
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
    assert executed == ["reviewer.md", "reviewer.md"]
    assert state.last_prompt == "reviewer.md"


def test_router_override_applies_to_steps(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    state = OrchestrationState(workflow_name="standard", step_index=0, iteration=1, status="idle")
    allowlist = ["supervisor.md", "main.md", "reviewer.md"]

    galph_ctx, ralph_ctx = _build_contexts(
        prompts_dir=prompts_dir,
        allowlist=allowlist,
        review_every_n_cycles=0,
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
    assert executed == ["reviewer.md", "reviewer.md"]


def test_router_disabled_uses_workflow_prompts(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    state = OrchestrationState(workflow_name="standard", step_index=0, iteration=1, status="idle")
    allowlist = ["supervisor.md", "main.md", "reviewer.md"]

    galph_ctx, ralph_ctx = _build_contexts(
        prompts_dir=prompts_dir,
        allowlist=allowlist,
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

    state = OrchestrationState(workflow_name="standard", step_index=0, iteration=1, status="idle")
    allowlist = ["supervisor.md", "main.md", "reviewer.md"]

    galph_ctx, ralph_ctx = _build_contexts(
        prompts_dir=prompts_dir,
        allowlist=allowlist,
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
    assert state.step_index == 0
    assert state.iteration == 1
    assert state.expected_step is None
    assert any("galph turn failed" in msg for msg in errors)


def test_router_only_without_output_marks_failed(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    state = OrchestrationState(workflow_name="standard", step_index=0, iteration=1, status="idle")
    allowlist = ["supervisor.md", "main.md", "reviewer.md"]

    galph_ctx, ralph_ctx = _build_contexts(
        prompts_dir=prompts_dir,
        allowlist=allowlist,
        router_mode="router_only",
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
    assert state.step_index == 0
    assert state.iteration == 1
    assert any("router_only" in msg for msg in errors)


def test_combined_autocommit_after_turns(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in ("supervisor.md", "main.md", "reviewer.md"):
        _write_prompt(prompts_dir / name)

    state = OrchestrationState(workflow_name="standard", step_index=0, iteration=1, status="idle")
    allowlist = ["supervisor.md", "main.md", "reviewer.md"]

    galph_ctx, ralph_ctx = _build_contexts(
        prompts_dir=prompts_dir,
        allowlist=allowlist,
    )

    calls: list[str] = []

    def _post_turn(role: str, _: OrchestrationState, __, ___: str) -> None:
        calls.append(role)

    rc = run_combined_iteration(
        state=state,
        galph_ctx=galph_ctx,
        ralph_ctx=ralph_ctx,
        galph_executor=lambda _: 0,
        ralph_executor=lambda _: 0,
        galph_logger=lambda _: None,
        ralph_logger=lambda _: None,
        state_writer=lambda _: None,
        post_turn=_post_turn,
    )

    assert rc == 0
    assert calls == ["galph", "ralph"]


def test_combined_autocommit_no_git(tmp_path: Path, monkeypatch) -> None:
    calls = {"docs": 0, "reports": 0, "tracked": 0}

    def _docs(**_) -> tuple[bool, list[str], list[str]]:
        calls["docs"] += 1
        return False, [], []

    def _reports(**_) -> tuple[bool, list[str], list[str]]:
        calls["reports"] += 1
        return False, [], []

    def _tracked(**_) -> tuple[bool, list[str], list[str]]:
        calls["tracked"] += 1
        return False, [], []

    monkeypatch.setattr(orchestrator_module, "autocommit_docs", _docs)
    monkeypatch.setattr(orchestrator_module, "autocommit_reports", _reports)
    monkeypatch.setattr(orchestrator_module, "autocommit_tracked_outputs", _tracked)

    config = CombinedAutoCommitConfig(
        auto_commit_docs=True,
        auto_commit_reports=True,
        auto_commit_tracked_outputs=True,
        dry_run=False,
        no_git=True,
        doc_whitelist=["docs/**/*.md"],
        max_autocommit_bytes=1024,
        report_extensions={".md"},
        report_path_globs=(),
        max_report_file_bytes=1024,
        max_report_total_bytes=2048,
        force_add_reports=False,
        tracked_output_globs=["tests/fixtures/**/*.npz"],
        tracked_output_extensions={".npz"},
        max_tracked_output_file_bytes=1024,
        max_tracked_output_total_bytes=2048,
        logdir_prefix_parts=(),
        state_file=tmp_path / "state.json",
    )

    logs: list[str] = []
    run_combined_autocommit(
        role="galph",
        logger=logs.append,
        config=config,
        iteration=1,
        prompt_name="supervisor.md",
    )

    assert calls == {"docs": 0, "reports": 0, "tracked": 0}
    assert any("no-git" in msg for msg in logs)


def test_combined_autocommit_dry_run(tmp_path: Path, monkeypatch) -> None:
    seen = {"docs": None, "reports": None, "tracked": None}

    def _docs(**kwargs) -> tuple[bool, list[str], list[str]]:
        seen["docs"] = kwargs.get("dry_run")
        return False, [], []

    def _reports(**kwargs) -> tuple[bool, list[str], list[str]]:
        seen["reports"] = kwargs.get("dry_run")
        return False, [], []

    def _tracked(**kwargs) -> tuple[bool, list[str], list[str]]:
        seen["tracked"] = kwargs.get("dry_run")
        return False, [], []

    monkeypatch.setattr(orchestrator_module, "autocommit_docs", _docs)
    monkeypatch.setattr(orchestrator_module, "autocommit_reports", _reports)
    monkeypatch.setattr(orchestrator_module, "autocommit_tracked_outputs", _tracked)

    config = CombinedAutoCommitConfig(
        auto_commit_docs=True,
        auto_commit_reports=True,
        auto_commit_tracked_outputs=True,
        dry_run=True,
        no_git=False,
        doc_whitelist=["docs/**/*.md"],
        max_autocommit_bytes=1024,
        report_extensions={".md"},
        report_path_globs=(),
        max_report_file_bytes=1024,
        max_report_total_bytes=2048,
        force_add_reports=False,
        tracked_output_globs=["tests/fixtures/**/*.npz"],
        tracked_output_extensions={".npz"},
        max_tracked_output_file_bytes=1024,
        max_tracked_output_total_bytes=2048,
        logdir_prefix_parts=(),
        state_file=tmp_path / "state.json",
    )

    run_combined_autocommit(
        role="galph",
        logger=lambda _: None,
        config=config,
        iteration=7,
        prompt_name="main.md",
    )

    assert seen == {"docs": True, "reports": True, "tracked": True}


def test_combined_autocommit_includes_iteration(tmp_path: Path, monkeypatch) -> None:
    seen = {"docs": None, "reports": None, "tracked": None}

    def _docs(**kwargs) -> tuple[bool, list[str], list[str]]:
        seen["docs"] = kwargs.get("commit_message_prefix")
        return False, [], []

    def _reports(**kwargs) -> tuple[bool, list[str], list[str]]:
        seen["reports"] = kwargs.get("commit_message_prefix")
        return False, [], []

    def _tracked(**kwargs) -> tuple[bool, list[str], list[str]]:
        seen["tracked"] = kwargs.get("commit_message_prefix")
        return False, [], []

    monkeypatch.setattr(orchestrator_module, "autocommit_docs", _docs)
    monkeypatch.setattr(orchestrator_module, "autocommit_reports", _reports)
    monkeypatch.setattr(orchestrator_module, "autocommit_tracked_outputs", _tracked)

    config = CombinedAutoCommitConfig(
        auto_commit_docs=True,
        auto_commit_reports=True,
        auto_commit_tracked_outputs=True,
        dry_run=False,
        no_git=False,
        doc_whitelist=["docs/**/*.md"],
        max_autocommit_bytes=1024,
        report_extensions={".md"},
        report_path_globs=(),
        max_report_file_bytes=1024,
        max_report_total_bytes=2048,
        force_add_reports=False,
        tracked_output_globs=["tests/fixtures/**/*.npz"],
        tracked_output_extensions={".npz"},
        max_tracked_output_file_bytes=1024,
        max_tracked_output_total_bytes=2048,
        logdir_prefix_parts=(),
        state_file=tmp_path / "state.json",
    )

    run_combined_autocommit(
        role="galph",
        logger=lambda _: None,
        config=config,
        iteration=12,
        prompt_name="supervisor.md",
    )

    for prefix in seen.values():
        assert prefix is not None
        assert prefix.startswith("SUPERVISOR AUTO")
        assert "iter=00012" in prefix
        assert "prompt=supervisor.md" in prefix


def test_combined_autocommit_role_prefix(tmp_path: Path, monkeypatch) -> None:
    seen = {"docs": None, "reports": None, "tracked": None}

    def _docs(**kwargs) -> tuple[bool, list[str], list[str]]:
        seen["docs"] = kwargs.get("commit_message_prefix")
        return False, [], []

    def _reports(**kwargs) -> tuple[bool, list[str], list[str]]:
        seen["reports"] = kwargs.get("commit_message_prefix")
        return False, [], []

    def _tracked(**kwargs) -> tuple[bool, list[str], list[str]]:
        seen["tracked"] = kwargs.get("commit_message_prefix")
        return False, [], []

    monkeypatch.setattr(orchestrator_module, "autocommit_docs", _docs)
    monkeypatch.setattr(orchestrator_module, "autocommit_reports", _reports)
    monkeypatch.setattr(orchestrator_module, "autocommit_tracked_outputs", _tracked)

    config = CombinedAutoCommitConfig(
        auto_commit_docs=True,
        auto_commit_reports=True,
        auto_commit_tracked_outputs=True,
        dry_run=False,
        no_git=False,
        doc_whitelist=["docs/**/*.md"],
        max_autocommit_bytes=1024,
        report_extensions={".md"},
        report_path_globs=(),
        max_report_file_bytes=1024,
        max_report_total_bytes=2048,
        force_add_reports=False,
        tracked_output_globs=["tests/fixtures/**/*.npz"],
        tracked_output_extensions={".npz"},
        max_tracked_output_file_bytes=1024,
        max_tracked_output_total_bytes=2048,
        logdir_prefix_parts=(),
        state_file=tmp_path / "state.json",
    )

    run_combined_autocommit(
        role="ralph",
        logger=lambda _: None,
        config=config,
        iteration=3,
        prompt_name="main.md",
    )

    for prefix in seen.values():
        assert prefix is not None
        assert prefix.startswith("RALPH AUTO")
        assert "prompt=main.md" in prefix


def test_combined_autocommit_flag_plumbing(tmp_path: Path) -> None:
    args = argparse.Namespace(
        auto_commit_docs=False,
        auto_commit_reports=True,
        auto_commit_tracked_outputs=False,
        commit_dry_run=True,
        no_git=False,
        autocommit_whitelist="input.md,docs/*.md",
        max_autocommit_bytes=777,
        report_extensions=".md,.json",
        report_path_globs="plans/**,reports/**",
        max_report_file_bytes=11,
        max_report_total_bytes=22,
        force_add_reports=False,
        tracked_output_globs="tests/fixtures/**/*.npz",
        tracked_output_extensions=".npz,.json",
        max_tracked_output_file_bytes=33,
        max_tracked_output_total_bytes=44,
        logdir=Path("logs"),
        state_file=tmp_path / "state.json",
    )
    cfg = OrchConfig()

    config = orchestrator_module._build_autocommit_config(args, cfg)

    assert config.auto_commit_docs is False
    assert config.auto_commit_reports is True
    assert config.auto_commit_tracked_outputs is False
    assert config.dry_run is True
    assert config.no_git is False
    assert config.doc_whitelist == ["input.md", "docs/*.md"]
    assert config.max_autocommit_bytes == 777
    assert config.report_extensions == {".md", ".json"}
    assert config.report_path_globs == ("plans/**", "reports/**")
    assert config.max_report_file_bytes == 11
    assert config.max_report_total_bytes == 22
    assert config.force_add_reports is False
    assert config.tracked_output_globs == ["tests/fixtures/**/*.npz"]
    assert config.tracked_output_extensions == {".json", ".npz"}
    assert config.max_tracked_output_file_bytes == 33
    assert config.max_tracked_output_total_bytes == 44
    assert config.logdir_prefix_parts == ("logs",)
    assert config.state_file == tmp_path / "state.json"


def test_combined_autocommit_best_effort(tmp_path: Path, monkeypatch) -> None:
    logs: list[str] = []

    def _docs(**_) -> tuple[bool, list[str], list[str]]:
        return False, [], ["outputs/bad.txt"]

    monkeypatch.setattr(orchestrator_module, "autocommit_docs", _docs)

    config = CombinedAutoCommitConfig(
        auto_commit_docs=True,
        auto_commit_reports=False,
        auto_commit_tracked_outputs=False,
        dry_run=False,
        no_git=False,
        doc_whitelist=["docs/**/*.md"],
        max_autocommit_bytes=1024,
        report_extensions={".md"},
        report_path_globs=(),
        max_report_file_bytes=1024,
        max_report_total_bytes=2048,
        force_add_reports=False,
        tracked_output_globs=[],
        tracked_output_extensions=set(),
        max_tracked_output_file_bytes=1024,
        max_tracked_output_total_bytes=2048,
        logdir_prefix_parts=(),
        state_file=tmp_path / "state.json",
    )

    run_combined_autocommit(
        role="ralph",
        logger=logs.append,
        config=config,
        iteration=2,
        prompt_name="main.md",
    )

    assert any("non-whitelist" in msg for msg in logs)


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


def test_resolve_use_pty_auto_avoids_claude() -> None:
    assert resolve_use_pty("claude", "auto") is False
    assert resolve_use_pty("codex", "auto") is None
    assert resolve_use_pty("claude", "always") is True
    assert resolve_use_pty("claude", "never") is False
