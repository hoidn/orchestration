#!/usr/bin/env python3
"""
Input Checker — Verify input.md includes a "Findings Applied" section
listing findings IDs from docs/findings.md or an explicit none-statement.
"""
import argparse
import os
import re
import sys

from .config import load_config


SECTION_RE = re.compile(r"^\s*-\s*Findings Applied", re.IGNORECASE)
FINDING_ID_RE = re.compile(r"\b[A-Z]+-[A-Z]+-\d+\b")


def main() -> int:
    # Load orchestration config (searches upward for orchestration.yaml)
    cfg = load_config(warn_missing=False)

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(cfg.input_file), help="Path to input.md")
    ap.add_argument("--findings", default=str(cfg.findings_file), help="Path to findings.md")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        print(f"ERROR: {args.input} not found", file=sys.stderr)
        return 2

    with open(args.input, "r", encoding="utf-8") as fh:
        content = fh.read()

    # Locate section presence
    if "Findings Applied" not in content:
        print("ERROR: input.md missing 'Findings Applied' section", file=sys.stderr)
        return 3

    # Search for IDs or explicit none statement
    ids = FINDING_ID_RE.findall(content)
    if not ids and "No relevant findings" not in content:
        print("ERROR: 'Findings Applied' must list finding IDs or state 'No relevant findings'", file=sys.stderr)
        return 4

    print("OK: 'Findings Applied' present and populated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

