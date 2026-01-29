from __future__ import annotations

import argparse
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List


def _ensure_flag(args: List[str], flag: str, value: str | None = None) -> List[str]:
    if flag in args:
        return args
    if value is None:
        return args + [flag]
    return args + [flag, value]


def _replace_flag(args: List[str], flag: str, value: str) -> List[str]:
    cleaned: List[str] = []
    skip_next = False
    for idx, token in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if token == flag and idx + 1 < len(args):
            skip_next = True
            continue
        cleaned.append(token)
    cleaned += [flag, value]
    return cleaned


def _forward_stderr(proc: subprocess.Popen) -> None:
    if proc.stderr is None:
        return
    while True:
        chunk = proc.stderr.read(4096)
        if not chunk:
            break
        sys.stderr.buffer.write(chunk)
        sys.stderr.buffer.flush()


def _resolve_engineer_summary_path() -> Path | None:
    raw = os.getenv("ORCHESTRATION_ENGINEER_SUMMARY_PATH", "engineer_summary.md").strip()
    if not raw or raw.lower() in {"0", "false", "no", "off"}:
        return None
    return Path(raw)


def _summary_updated(
    path: Path,
    start_ns: int,
    initial_mtime_ns: int | None,
) -> bool:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False
    mtime_ns = stat.st_mtime_ns
    if initial_mtime_ns is not None and mtime_ns <= initial_mtime_ns:
        return False
    if mtime_ns < start_ns:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Claude CLI in stream-json mode and stop on message_stop.")
    parser.add_argument("--claude", required=True, help="Path to Claude CLI binary.")
    parser.add_argument("claude_args", nargs=argparse.REMAINDER, help="Args passed to Claude after '--'.")
    args = parser.parse_args()

    claude_args = args.claude_args
    if claude_args and claude_args[0] == "--":
        claude_args = claude_args[1:]

    claude_args = _ensure_flag(claude_args, "-p")
    claude_args = _replace_flag(claude_args, "--output-format", "stream-json")
    claude_args = _ensure_flag(claude_args, "--include-partial-messages")

    proc = subprocess.Popen(
        [args.claude] + claude_args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
    )

    stderr_thread = threading.Thread(target=_forward_stderr, args=(proc,), daemon=True)
    stderr_thread.start()

    if proc.stdin is not None:
        payload = sys.stdin.buffer.read()
        if payload:
            proc.stdin.write(payload)
        proc.stdin.close()

    saw_stop = False
    saw_summary = False
    start_ns = time.time_ns()
    summary_path = _resolve_engineer_summary_path()
    summary_mtime_ns = None
    if summary_path is not None and summary_path.exists():
        summary_mtime_ns = summary_path.stat().st_mtime_ns

    buffer = b""
    stdout_fd = proc.stdout.fileno() if proc.stdout is not None else None
    while True:
        if summary_path is not None and _summary_updated(summary_path, start_ns, summary_mtime_ns):
            saw_summary = True
            break
        if proc.poll() is not None:
            break
        if stdout_fd is None:
            time.sleep(0.1)
            continue
        readable, _, _ = select.select([stdout_fd], [], [], 0.1)
        if stdout_fd not in readable:
            continue
        try:
            chunk = os.read(stdout_fd, 4096)
        except OSError:
            chunk = b""
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            raw_line, buffer = buffer.split(b"\n", 1)
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                sys.stdout.buffer.write(raw_line + b"\n")
                sys.stdout.buffer.flush()
                continue

            etype = event.get("type")
            if etype == "content_block_delta":
                delta = event.get("delta") or {}
                text = delta.get("text") or ""
                if text:
                    sys.stdout.write(text)
                    sys.stdout.flush()
            elif etype == "error":
                msg = (event.get("error") or {}).get("message")
                if msg:
                    sys.stderr.write(msg + "\n")
                    sys.stderr.flush()
            elif etype in {"message_stop", "response_stop"}:
                saw_stop = True
                break
        if saw_stop:
            break

    if buffer.strip() and not saw_stop:
        try:
            event = json.loads(buffer.strip())
        except Exception:
            sys.stdout.buffer.write(buffer)
            sys.stdout.buffer.flush()
        else:
            etype = event.get("type")
            if etype == "content_block_delta":
                delta = event.get("delta") or {}
                text = delta.get("text") or ""
                if text:
                    sys.stdout.write(text)
                    sys.stdout.flush()
            elif etype == "error":
                msg = (event.get("error") or {}).get("message")
                if msg:
                    sys.stderr.write(msg + "\n")
                    sys.stderr.flush()
            elif etype in {"message_stop", "response_stop"}:
                saw_stop = True

    if saw_stop or saw_summary:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
        proc.wait(timeout=5)

    returncode = proc.returncode if proc.returncode is not None else 0
    if saw_stop or saw_summary:
        return 0
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
