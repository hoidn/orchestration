from __future__ import annotations

import json
import sys


def main() -> None:
    """
    Convert Claude CLI --output-format stream-json events into plain text.

    This is a best-effort, streaming filter:
    - For each JSON line with type == "content_block_delta", emit delta["text"].
    - For non-JSON lines, pass them through unchanged.
    - For error events, print the error message to stderr.
    """
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except Exception:
            # Not JSON — pass through as-is
            sys.stdout.write(raw)
            sys.stdout.flush()
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

    # Ensure a trailing newline for well-formed logs/TTY
    sys.stdout.write("\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()

