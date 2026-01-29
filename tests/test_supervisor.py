"""Tests for supervisor.py --no-git functionality.

Verifies that when --no-git is set, the supervisor skips all git operations
(branch guard, pull, submodule scrub, add, commit, push) while still running
prompt execution and logging.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.orchestration import supervisor as supervisor_module
from scripts.orchestration.state import OrchestrationState


class TestNoGit:
    """Test suite for supervisor --no-git mode."""

    def test_sync_iteration_skips_git_ops(self, tmp_path: Path, monkeypatch) -> None:
        """Verify supervisor with --no-git skips all git bus calls.

        When --no-git is set:
        - Branch guard should be skipped
        - _pull_with_error should never be called
        - _submodule_scrub should never be called
        - add/commit/push should never be called
        - autocommit_reports should never be called
        - Prompt execution (tee_run) should still run
        """
        # Setup test prompts directory
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "supervisor.md").write_text("test prompt", encoding="utf-8")
        monkeypatch.delenv("ORCHESTRATION_WORKFLOW", raising=False)

        # Setup state file
        state_dir = tmp_path / "sync"
        state_dir.mkdir()
        state_file = state_dir / "state.json"
        st = OrchestrationState(workflow_name="standard", step_index=0, iteration=1, status="idle")
        st.write(str(state_file))

        # Track git bus calls
        git_calls = {
            "assert_on_branch": 0,
            "safe_pull": 0,
            "add": 0,
            "commit": 0,
            "push_to": 0,
            "push_with_rebase": 0,
            "short_head": 0,
            "has_unpushed_commits": 0,
            "current_branch": 0,
        }

        def _track(name: str):
            def _inner(*args, **kwargs):
                git_calls[name] += 1
                if name == "safe_pull":
                    return True
                if name == "has_unpushed_commits":
                    return False
                if name == "current_branch":
                    return "main"
                if name == "short_head":
                    return "abc1234"
                return None
            return _inner

        # Mock git bus functions
        monkeypatch.setattr(supervisor_module, "assert_on_branch", _track("assert_on_branch"))
        monkeypatch.setattr(supervisor_module, "safe_pull", _track("safe_pull"))
        monkeypatch.setattr(supervisor_module, "add", _track("add"))
        monkeypatch.setattr(supervisor_module, "commit", _track("commit"))
        monkeypatch.setattr(supervisor_module, "push_to", _track("push_to"))
        monkeypatch.setattr(supervisor_module, "push_with_rebase", _track("push_with_rebase"))
        monkeypatch.setattr(supervisor_module, "short_head", _track("short_head"))
        monkeypatch.setattr(supervisor_module, "has_unpushed_commits", _track("has_unpushed_commits"))
        monkeypatch.setattr(supervisor_module, "current_branch", _track("current_branch"))

        # Mock autocommit_reports to track calls
        autocommit_calls = {"reports": 0}

        def _mock_autocommit(**kwargs):
            autocommit_calls["reports"] += 1

        monkeypatch.setattr(supervisor_module, "autocommit_reports", _mock_autocommit)

        # Track tee_run calls to verify prompt execution still happens
        tee_run_calls = []

        def _mock_tee_run(cmd, prompt_path, log_path, *, use_pty=None):
            tee_run_calls.append((cmd, str(prompt_path), str(log_path), use_pty))
            return 0

        monkeypatch.setattr(supervisor_module, "tee_run", _mock_tee_run)

        # Mock resolve_cmd to return a simple command
        def _mock_resolve_cmd(*args, **kwargs):
            return ["echo", "test"]

        monkeypatch.setattr(supervisor_module, "resolve_cmd", _mock_resolve_cmd)

        # Mock load_config to return test paths
        from scripts.orchestration.config import OrchConfig
        cfg = OrchConfig()
        cfg.prompts_dir = prompts_dir
        cfg.state_file = state_file
        cfg.supervisor_prompt = "supervisor.md"
        monkeypatch.setattr(supervisor_module, "load_config", lambda warn_missing=True: cfg)

        # Set CLI args for --no-git --sync-via-git mode
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "supervisor",
                "--sync-via-git",
                "--no-git",
                "--sync-loops", "1",
                "--state-file", str(state_file),
                "--logdir", str(tmp_path / "logs"),
            ],
        )

        # Run supervisor
        rc = supervisor_module.main()

        # Verify no git operations occurred
        assert git_calls["assert_on_branch"] == 0, "Branch guard should be skipped with --no-git"
        assert git_calls["safe_pull"] == 0, "safe_pull should not be called with --no-git"
        assert git_calls["add"] == 0, "git add should not be called with --no-git"
        assert git_calls["commit"] == 0, "git commit should not be called with --no-git"
        assert git_calls["push_to"] == 0, "push_to should not be called with --no-git"
        assert git_calls["push_with_rebase"] == 0, "push_with_rebase should not be called with --no-git"
        assert git_calls["short_head"] == 0, "short_head should not be called with --no-git"
        assert autocommit_calls["reports"] == 0, "autocommit_reports should not be called with --no-git"

        # Verify prompt execution still happened
        assert len(tee_run_calls) == 1, "tee_run should be called once"
        assert "supervisor.md" in tee_run_calls[0][1], "Should execute supervisor prompt"

        # Verify state was updated locally
        final_state = OrchestrationState.read(str(state_file))
        assert final_state.step_index > 0, "Step index should advance"
        assert final_state.iteration == final_state.step_index + 1, "Iteration should track step_index"
        assert final_state.status in {"waiting-next", "complete"}

        assert rc == 0

    def test_no_git_legacy_mode_runs_prompts(self, tmp_path: Path, monkeypatch) -> None:
        """Verify legacy (non-sync) mode with --no-git runs prompts without git ops."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "supervisor.md").write_text("test prompt", encoding="utf-8")
        monkeypatch.delenv("ORCHESTRATION_WORKFLOW", raising=False)

        git_calls = {"current_branch": 0, "assert_on_branch": 0}

        def _track_current_branch():
            git_calls["current_branch"] += 1
            return "main"

        def _track_assert_on_branch(*args, **kwargs):
            git_calls["assert_on_branch"] += 1

        monkeypatch.setattr(supervisor_module, "current_branch", _track_current_branch)
        monkeypatch.setattr(supervisor_module, "assert_on_branch", _track_assert_on_branch)

        tee_run_calls = []

        def _mock_tee_run(cmd, prompt_path, log_path, *, use_pty=None):
            tee_run_calls.append(str(prompt_path))
            return 0

        monkeypatch.setattr(supervisor_module, "tee_run", _mock_tee_run)

        def _mock_resolve_cmd(*args, **kwargs):
            return ["echo", "test"]

        monkeypatch.setattr(supervisor_module, "resolve_cmd", _mock_resolve_cmd)

        from scripts.orchestration.config import OrchConfig
        cfg = OrchConfig()
        cfg.prompts_dir = prompts_dir
        cfg.supervisor_prompt = "supervisor.md"
        monkeypatch.setattr(supervisor_module, "load_config", lambda warn_missing=True: cfg)

        # Legacy mode (no --sync-via-git) with --no-git
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "supervisor",
                "--no-git",
                "--sync-loops", "1",
                "--branch", "test-branch",
            ],
        )

        rc = supervisor_module.main()

        # Branch guard should be skipped with --no-git
        assert git_calls["assert_on_branch"] == 0, "Branch guard should be skipped"
        # Prompt should still execute
        assert len(tee_run_calls) == 1
        assert rc == 0

    def test_no_git_state_updated_locally(self, tmp_path: Path, monkeypatch) -> None:
        """Verify state.json is updated locally when --no-git is set."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "supervisor.md").write_text("test prompt", encoding="utf-8")
        monkeypatch.delenv("ORCHESTRATION_WORKFLOW", raising=False)

        state_dir = tmp_path / "sync"
        state_dir.mkdir()
        state_file = state_dir / "state.json"
        st = OrchestrationState(workflow_name="standard", step_index=4, iteration=5, status="idle")
        st.write(str(state_file))

        # Mock all git operations to fail if called
        def _fail_git(*args, **kwargs):
            raise RuntimeError("Git operation called when --no-git is set")

        monkeypatch.setattr(supervisor_module, "safe_pull", _fail_git)
        monkeypatch.setattr(supervisor_module, "add", _fail_git)
        monkeypatch.setattr(supervisor_module, "commit", _fail_git)
        monkeypatch.setattr(supervisor_module, "push_to", _fail_git)
        monkeypatch.setattr(supervisor_module, "push_with_rebase", _fail_git)

        monkeypatch.setattr(supervisor_module, "tee_run", lambda *a, **k: 0)
        monkeypatch.setattr(supervisor_module, "resolve_cmd", lambda *a, **k: ["echo"])

        from scripts.orchestration.config import OrchConfig
        cfg = OrchConfig()
        cfg.prompts_dir = prompts_dir
        cfg.state_file = state_file
        cfg.supervisor_prompt = "supervisor.md"
        monkeypatch.setattr(supervisor_module, "load_config", lambda warn_missing=True: cfg)

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "supervisor",
                "--sync-via-git",
                "--no-git",
                "--sync-loops", "1",
                "--state-file", str(state_file),
                "--logdir", str(tmp_path / "logs"),
            ],
        )

        rc = supervisor_module.main()

        # State should be updated locally
        final_state = OrchestrationState.read(str(state_file))
        assert final_state.step_index > st.step_index
        assert final_state.iteration == final_state.step_index + 1
        assert final_state.status in {"waiting-next", "complete"}
        assert rc == 0

    def test_no_git_autocommit_skipped(self, tmp_path: Path, monkeypatch) -> None:
        """Verify auto-commit operations are skipped with --no-git."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "supervisor.md").write_text("test prompt", encoding="utf-8")
        monkeypatch.delenv("ORCHESTRATION_WORKFLOW", raising=False)

        state_dir = tmp_path / "sync"
        state_dir.mkdir()
        state_file = state_dir / "state.json"
        st = OrchestrationState(workflow_name="standard", step_index=0, iteration=1, status="idle")
        st.write(str(state_file))

        autocommit_calls = {"docs": 0, "reports": 0, "tracked": 0}

        def _mock_autocommit_reports(**kwargs):
            autocommit_calls["reports"] += 1

        monkeypatch.setattr(supervisor_module, "autocommit_reports", _mock_autocommit_reports)

        # Mock safe_pull to fail if called (should be skipped)
        def _fail_pull(*args):
            raise RuntimeError("safe_pull should not be called")

        monkeypatch.setattr(supervisor_module, "safe_pull", _fail_pull)
        monkeypatch.setattr(supervisor_module, "add", lambda *a: None)
        monkeypatch.setattr(supervisor_module, "commit", lambda *a: True)
        monkeypatch.setattr(supervisor_module, "push_to", lambda *a, **k: None)
        monkeypatch.setattr(supervisor_module, "push_with_rebase", lambda *a, **k: True)

        monkeypatch.setattr(supervisor_module, "tee_run", lambda *a, **k: 0)
        monkeypatch.setattr(supervisor_module, "resolve_cmd", lambda *a, **k: ["echo"])

        from scripts.orchestration.config import OrchConfig
        cfg = OrchConfig()
        cfg.prompts_dir = prompts_dir
        cfg.state_file = state_file
        cfg.supervisor_prompt = "supervisor.md"
        monkeypatch.setattr(supervisor_module, "load_config", lambda warn_missing=True: cfg)

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "supervisor",
                "--sync-via-git",
                "--no-git",
                "--sync-loops", "1",
                "--state-file", str(state_file),
                "--logdir", str(tmp_path / "logs"),
                "--auto-commit-reports",
                "--auto-commit-docs",
            ],
        )

        rc = supervisor_module.main()

        # Auto-commit should be skipped
        assert autocommit_calls["reports"] == 0, "autocommit_reports should be skipped with --no-git"
        assert rc == 0
