from __future__ import annotations

import fnmatch
import os
from subprocess import run, PIPE
from pathlib import PurePosixPath
from typing import Callable, Iterable, Tuple, List, Set, Optional


def _run_list(cmd: Iterable[str]) -> List[str]:
    cp = run(list(cmd), stdout=PIPE, stderr=PIPE, text=True)
    if cp.returncode != 0:
        return []
    return [p for p in (cp.stdout or "").splitlines() if p.strip()]


def list_dirty_paths(include_ignored_untracked: bool = False) -> Tuple[List[str], List[str], List[str], List[str]]:
    """
    Return (unstaged_mod, staged_mod, untracked, ignored_untracked) path lists.
    If include_ignored_untracked is True, also list ignored untracked files.
    """
    unstaged_mod = _run_list(["git", "diff", "--name-only", "--diff-filter=M"])
    staged_mod = _run_list(["git", "diff", "--cached", "--name-only", "--diff-filter=AM"])
    untracked = _run_list(["git", "ls-files", "--others", "--exclude-standard"])
    ignored_untracked: List[str] = []
    if include_ignored_untracked:
        # list ignored, untracked files according to .gitignore
        ignored_untracked = _run_list(["git", "ls-files", "--others", "-i", "--exclude-standard"])
    return unstaged_mod, staged_mod, untracked, ignored_untracked


def gitlink_paths() -> Set[str]:
    """Return submodule gitlink paths recorded in the git index."""
    paths: Set[str] = set()
    try:
        for line in _run_list(["git", "ls-files", "-s"]):
            parts = line.split()
            if len(parts) >= 4 and parts[0] == "160000":
                paths.add(parts[3])
    except Exception:
        return set()
    return paths


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)


def _filter_submodule_paths(paths: Iterable[str], gitlinks: Set[str]) -> List[str]:
    if not gitlinks:
        return list(paths)
    filtered: List[str] = []
    for p in paths:
        in_submodule = False
        for sp in gitlinks:
            if p == sp or p.startswith(sp + os.sep):
                in_submodule = True
                break
        if not in_submodule:
            filtered.append(p)
    return filtered


def autocommit_docs(
    *,
    whitelist_globs: Iterable[str],
    max_file_bytes: int,
    logger: Callable[[str], None],
    commit_message_prefix: str = "SUPERVISOR AUTO: doc/meta hygiene — tests: not run",
    dry_run: bool = False,
    ignore_paths: Optional[Iterable[str]] = None,
) -> Tuple[bool, List[str], List[str]]:
    """
    Stage and commit doc/meta whitelist changes.
    Returns (committed, allowed_paths, forbidden_paths).
    """
    ignore_set = {p for p in (ignore_paths or []) if p}
    unstaged_mod, staged_mod, untracked, _ = list_dirty_paths()
    dirty_all = sorted(set(unstaged_mod) | set(staged_mod) | set(untracked))
    dirty_all = [p for p in dirty_all if p not in ignore_set]
    dirty_all = _filter_submodule_paths(dirty_all, gitlink_paths())
    whitelist = [p for p in whitelist_globs if p]
    allowed: List[str] = []
    forbidden: List[str] = []
    for p in dirty_all:
        if _matches_any(p, whitelist):
            try:
                if os.path.isfile(p) and os.path.getsize(p) <= max_file_bytes:
                    allowed.append(p)
                else:
                    forbidden.append(p)
            except FileNotFoundError:
                forbidden.append(p)
        else:
            forbidden.append(p)

    if not allowed:
        return False, [], forbidden

    if dry_run:
        logger(f"[docs] DRY-RUN: would commit {len(allowed)} files")
        return False, allowed, forbidden

    from .git_bus import add, commit

    add(allowed)
    body = "\n\nFiles:\n" + "\n".join(f" - {p}" for p in allowed)
    committed = commit(f"{commit_message_prefix}{body}")
    if committed:
        logger(f"[docs] Auto-committed {len(allowed)} files")
    else:
        logger("[docs] WARNING: git commit failed; staged files remain staged")
    return committed, allowed, forbidden


def autocommit_tracked_outputs(
    *,
    tracked_output_globs: Iterable[str],
    tracked_output_extensions: Iterable[str],
    max_file_bytes: int,
    max_total_bytes: int,
    logger: Callable[[str], None],
    commit_message_prefix: str = "SUPERVISOR AUTO: tracked outputs — tests: not run",
    dry_run: bool = False,
) -> Tuple[bool, List[str], List[str]]:
    """
    Stage and commit modified tracked output files (fixtures, etc).
    Returns (committed, staged_paths, skipped_paths).
    """
    modified = _run_list(["git", "diff", "--name-only", "--diff-filter=M"])
    if not modified:
        return False, [], []

    path_globs = [p for p in tracked_output_globs if p]
    exts = {e.strip().lower() for e in tracked_output_extensions if e}
    staged: List[str] = []
    skipped: List[str] = []
    total_bytes = 0

    for p in modified:
        _, ext = os.path.splitext(p)
        if ext.lower() not in exts:
            skipped.append(p)
            continue
        if path_globs and not _matches_any(p, path_globs):
            skipped.append(p)
            continue
        try:
            if not os.path.isfile(p):
                skipped.append(p)
                continue
            size = os.path.getsize(p)
        except FileNotFoundError:
            skipped.append(p)
            continue
        if size > max_file_bytes or (total_bytes + size) > max_total_bytes:
            skipped.append(p)
            continue

        staged.append(p)
        total_bytes += size

    if not staged:
        return False, [], skipped

    if dry_run:
        logger(f"[tracked-outputs] DRY-RUN: would commit {len(staged)} files ({total_bytes} bytes)")
        return False, staged, skipped

    from .git_bus import add, commit

    add(staged)
    body = "\n\nFiles:\n" + "\n".join(f" - {x}" for x in staged)
    committed = commit(f"{commit_message_prefix}{body}")
    if committed:
        logger(f"[tracked-outputs] Auto-committed {len(staged)} files ({total_bytes} bytes)")
    else:
        logger("[tracked-outputs] WARNING: git commit failed; staged files remain staged")
    return committed, staged, skipped


def autocommit_reports(
    *,
    allowed_extensions: Set[str],
    max_file_bytes: int,
    max_total_bytes: int,
    force_add: bool,
    logger: Callable[[str], None],
    commit_message_prefix: str = "AUTO: reports evidence — tests: not run",
    skip_predicate: Optional[Callable[[str], bool]] = None,
    allowed_path_globs: Optional[Iterable[str]] = None,
    dry_run: bool = False,
) -> Tuple[bool, List[str], List[str]]:
    """
    Stage and commit report-like artifacts filtered by extension and size caps.
    The optional skip_predicate can suppress specific paths from staging.
    Returns (committed, staged_paths, skipped_paths).
    """
    # Normalize extensions and path allowlist
    allowed_exts = {e.lower() for e in allowed_extensions}
    path_globs: Tuple[str, ...] = tuple(g for g in (allowed_path_globs or []) if g)
    unstaged_mod, staged_mod, untracked, ignored_untracked = list_dirty_paths(include_ignored_untracked=force_add)
    dirty_all: List[str] = []
    seen: Set[str] = set()
    for p in unstaged_mod + staged_mod + untracked + ignored_untracked:
        if p not in seen:
            dirty_all.append(p)
            seen.add(p)

    staged: List[str] = []
    skipped: List[str] = []
    total_bytes = 0

    for p in dirty_all:
        if path_globs:
            posix_path = PurePosixPath(p)
            if not any(posix_path.match(glob) for glob in path_globs):
                skipped.append(p)
                continue
        if skip_predicate and skip_predicate(p):
            skipped.append(p)
            continue
        # Determine extension
        ext = os.path.splitext(p)[1].lower()
        if ext not in allowed_exts:
            skipped.append(p)
            continue
        try:
            if not os.path.isfile(p):
                skipped.append(p)
                continue
            size = os.path.getsize(p)
            if size > max_file_bytes or (total_bytes + size) > max_total_bytes:
                skipped.append(p)
                continue
        except FileNotFoundError:
            skipped.append(p)
            continue

        if dry_run:
            if not force_add:
                chk = run(["git", "check-ignore", "-q", p])
                if chk.returncode == 0:
                    skipped.append(p)
                    continue
            staged.append(p)
            total_bytes += size
            continue

        # check-ignore
        ignored = False
        if force_add:
            chk = run(["git", "check-ignore", "-q", p])
            ignored = (chk.returncode == 0)

        if ignored and force_add:
            add = run(["git", "add", "-f", p], stdout=PIPE, stderr=PIPE, text=True)
        else:
            add = run(["git", "add", p], stdout=PIPE, stderr=PIPE, text=True)
        if add.returncode != 0:
            skipped.append(p)
            continue
        staged.append(p)
        total_bytes += size

    committed = False
    if staged:
        if dry_run:
            logger(f"[reports] DRY-RUN: would commit {len(staged)} files ({total_bytes} bytes)")
            return False, staged, skipped
        body = "\n\nFiles:\n" + "\n".join(f" - {x}" for x in staged)
        from .git_bus import commit  # local import to avoid cycle concerns at module import
        committed = commit(f"{commit_message_prefix}{body}")
        if committed:
            logger(f"[reports] Auto-committed {len(staged)} files ({total_bytes} bytes)")
        else:
            logger("[reports] WARNING: git commit failed; staged files remain staged")
    return committed, staged, skipped
