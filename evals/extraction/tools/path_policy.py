#!/usr/bin/env python3
"""Shared private-path policy for calibration tooling.

Real calibration material — captures, gold labels, held-out cases, blind
relabel worksheets, order mappings, disagreement reports — must never sit
inside a Git worktree of this repository. Worksheets and diff reports
carry verbatim utterances and gold, so they are exactly as sensitive as
the dataset itself; restricting only the dataset path is not protection.

A repository can have MANY registered worktrees at unrelated filesystem
locations (the primary checkout plus every `git worktree add`). Checking
only the worktree a tool happens to run from would miss the others, so
the authoritative list comes from `git worktree list --porcelain`.

Policy properties:

* **Fail closed.** If the worktree list cannot be obtained, tools refuse
  to run rather than assume a path is safe.
* **Exit code 2** for every refusal (usage/policy error).
* **Named path in the message** — the caller is told which argument and
  which worktree collided.
* **No partial output.** Callers must guard every input and output path
  before opening anything for writing.

The one exception is the small set of public fixtures shipped in the
repo (the sample file and an optional in-repo synthetic fixture). Those
are public by construction, contain no real capture, and never count
toward a real baseline.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
EXTRACTION_DIR = TOOLS_DIR.parent
REPO_ROOT = TOOLS_DIR.parents[2]

# In-repo files that are public by construction. Everything else inside a
# worktree is refused. Public fixtures are synthetic/illustrative only and
# never count toward the real-capture minimum.
PUBLIC_FIXTURES: tuple[Path, ...] = (
    EXTRACTION_DIR / "dataset.sample.jsonl",
    EXTRACTION_DIR / "calibration" / "synthetic.jsonl",
)


class WorktreeDiscoveryError(RuntimeError):
    """Raised when the registered worktree list cannot be determined."""


class PrivatePathViolation(RuntimeError):
    """Raised when a private-data path resolves inside a worktree."""

    def __init__(self, label: str, path: Path, worktree: Path):
        self.label = label
        self.path = path
        self.worktree = worktree
        super().__init__(
            f"{label} path {path} is inside the registered Git worktree "
            f"{worktree}. Real captures, gold labels, relabel worksheets, "
            f"order mappings and disagreement reports must live outside "
            f"every worktree of this repository."
        )


def _norm(path: Path) -> str:
    """Case- and separator-normalised absolute path string.

    ``normcase`` matters on Windows, where ``D:/Repo`` and ``d:\\repo``
    are the same directory; a case-sensitive comparison would let a
    differently-cased path slip past the guard.
    """
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def is_within(path: Path, parent: Path) -> bool:
    """True if ``path`` is ``parent`` or lives underneath it."""
    p, q = _norm(path), _norm(parent)
    return p == q or p.startswith(q.rstrip(os.sep) + os.sep)


def registered_worktrees(repo_hint: Path | None = None) -> list[Path]:
    """Every worktree registered with this repository.

    Uses ``git worktree list --porcelain`` so the primary checkout and
    all linked worktrees are covered, wherever they live on disk.
    Raises :class:`WorktreeDiscoveryError` on any failure — callers must
    treat that as refusal, never as "no worktrees".
    """
    cwd = Path(repo_hint) if repo_hint else REPO_ROOT
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorktreeDiscoveryError(
            f"could not run 'git worktree list' in {cwd}: {exc}"
        ) from exc
    if proc.returncode != 0:
        raise WorktreeDiscoveryError(
            f"'git worktree list' failed in {cwd}: "
            f"{proc.stderr.strip() or proc.returncode}"
        )

    trees = [
        Path(line[len("worktree ") :].strip())
        for line in proc.stdout.splitlines()
        if line.startswith("worktree ")
    ]
    if not trees:
        raise WorktreeDiscoveryError(
            f"'git worktree list' returned no worktrees in {cwd}"
        )
    return trees


def is_public_fixture(path: Path) -> bool:
    """True for the small allowlist of public in-repo fixture files."""
    target = _norm(path)
    return any(target == _norm(fixture) for fixture in PUBLIC_FIXTURES)


def find_violation(
    label: str, path: Path, worktrees: list[Path]
) -> PrivatePathViolation | None:
    for tree in worktrees:
        if is_within(path, tree):
            return PrivatePathViolation(label, Path(path).resolve(), tree)
    return None


def assert_private_paths(
    labelled_paths: dict[str, Path], worktrees: list[Path] | None = None
) -> None:
    """Raise on the first path that resolves inside any worktree."""
    trees = registered_worktrees() if worktrees is None else worktrees
    for label, path in labelled_paths.items():
        violation = find_violation(label, Path(path), trees)
        if violation is not None:
            raise violation


def guard_private_paths(labelled_paths: dict[str, Path]) -> None:
    """CLI wrapper: enforce the policy or exit 2 before any file is opened."""
    try:
        assert_private_paths(labelled_paths)
    except WorktreeDiscoveryError as exc:
        print(
            f"ERROR: private-path policy cannot be verified ({exc}). "
            "Refusing to run — this check fails closed.",
            file=sys.stderr,
        )
        sys.exit(2)
    except PrivatePathViolation as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "No output was written. Point the path at your private "
            "calibration workspace (outside every worktree) and re-run.",
            file=sys.stderr,
        )
        sys.exit(2)
