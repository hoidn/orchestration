from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


ISO = "%Y-%m-%dT%H:%M:%SZ"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime(ISO)


def _lease_expires_iso(minutes: int = 10) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime(ISO)


@dataclass
class OrchestrationState:
    workflow_name: str = "standard"
    step_index: int = 0
    iteration: int = 1
    expected_step: Optional[str] = None
    status: str = "idle"  # "idle" | "running" | "waiting-next" | "complete" | "failed"
    last_update: str = field(default_factory=_utc_now_iso)
    lease_expires_at: str = field(default_factory=_lease_expires_iso)
    galph_commit: Optional[str] = None
    ralph_commit: Optional[str] = None
    last_prompt: Optional[str] = None

    @staticmethod
    def read(path: str) -> "OrchestrationState":
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return OrchestrationState()

        raw_iteration = int(data.get("iteration", 1))
        step_index = int(data.get("step_index", max(0, raw_iteration - 1)))
        iteration = int(data.get("iteration", step_index + 1))

        return OrchestrationState(
            workflow_name=str(data.get("workflow_name", "standard")),
            step_index=step_index,
            iteration=iteration,
            expected_step=data.get("expected_step"),
            status=str(data.get("status", "idle")),
            last_update=str(data.get("last_update", _utc_now_iso())),
            lease_expires_at=str(data.get("lease_expires_at", _lease_expires_iso())),
            galph_commit=data.get("galph_commit"),
            ralph_commit=data.get("ralph_commit"),
            last_prompt=data.get("last_prompt"),
        )

    def write(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(prefix="state.", suffix=".json", dir=os.path.dirname(path) or ".")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(self.__dict__, f, indent=2)
                f.write("\n")
            os.replace(tmp_path, path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    def stamp(
        self,
        *,
        expected_step: Optional[str] = None,
        status: Optional[str] = None,
        increment_step: bool = False,
        galph_commit: Optional[str] = None,
        ralph_commit: Optional[str] = None,
    ) -> None:
        if expected_step is not None:
            self.expected_step = expected_step
        if status is not None:
            self.status = status
        if increment_step:
            self.step_index += 1
            self.iteration = self.step_index + 1
        if galph_commit:
            self.galph_commit = galph_commit
        if ralph_commit:
            self.ralph_commit = ralph_commit
        self.last_update = _utc_now_iso()
        self.lease_expires_at = _lease_expires_iso()
