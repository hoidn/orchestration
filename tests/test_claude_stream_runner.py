from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def test_claude_stream_runner_exits_on_engineer_summary(tmp_path: Path) -> None:
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\nsleep 10\n", encoding="utf-8")
    fake_claude.chmod(0o755)

    summary_path = tmp_path / "engineer_summary.md"
    env = os.environ.copy()
    env["ORCHESTRATION_ENGINEER_SUMMARY_PATH"] = str(summary_path)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scripts.orchestration.claude_stream_runner",
            "--claude",
            str(fake_claude),
            "--",
            "-p",
        ],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    time.sleep(0.2)
    summary_path.write_text("done", encoding="utf-8")

    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise AssertionError("runner did not exit after engineer_summary update")

    assert proc.returncode == 0
