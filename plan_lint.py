#!/usr/bin/env python3
"""
Plan Linter — Enforce persistent planning discipline.

Checks:
- If input.md has more than N checklist lines, require a persistent
  plans/active/<initiative-id>/implementation.md with those IDs present.

This is a lightweight, best-effort linter; it does not parse Markdown fully.
"""
import argparse
import os
import re
import sys

from .config import load_config


CHECKLIST_RE = re.compile(r"^- \[.\] ([A-Z][0-9]+):")


def extract_checklist_ids(path: str):
    ids = []
    if not os.path.exists(path):
        return ids
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            m = CHECKLIST_RE.match(line.strip())
            if m:
                ids.append(m.group(1))
    return ids


def main() -> int:
    # Load orchestration config (searches upward for orchestration.yaml)
    cfg = load_config(warn_missing=False)

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(cfg.input_file), help="Path to input.md")
    ap.add_argument("--implementation", required=True, help="Path to implementation.md for the initiative")
    ap.add_argument("--max-inline", type=int, default=5, help="Max inline checklist items allowed in input.md")
    args = ap.parse_args()

    input_ids = extract_checklist_ids(args.input)
    if len(input_ids) <= args.max_inline:
        print("OK: inline checklist size within limit")
        return 0

    if not os.path.exists(args.implementation):
        print(f"ERROR: expected persistent plan at {args.implementation}", file=sys.stderr)
        return 2

    impl_ids = extract_checklist_ids(args.implementation)
    missing = [i for i in input_ids if i not in impl_ids]
    if missing:
        print(f"ERROR: checklist IDs missing from implementation.md: {', '.join(missing)}", file=sys.stderr)
        return 3

    print("OK: input.md references persistent implementation plan IDs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

