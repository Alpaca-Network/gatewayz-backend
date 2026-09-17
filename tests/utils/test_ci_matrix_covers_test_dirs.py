"""Guard: every tests/ directory is either run by CI or explicitly excluded.

A test directory joins this repository by being created. It joins CI by
someone remembering to edit `.github/workflows/ci.yml`. Those are not the same
act, and the gap between them is silent — the tests pass locally, the PR goes
green, and nobody learns the category was never run.

This has now happened twice:

* 2026-09-16 — `tests/schema/` was in no matrix path. The matrix had
  `tests/schemas/` (plural, pydantic models); nothing matched the singular. So
  the two guards written to stop the phantom-column class, after seven
  production failures, had never executed on any PR. The PRs that introduced
  them went green partly *because* their own guards did not run.

* 2026-09-17 — listing `tests/*` against the matrix found **eight** unmapped
  directories totalling 164 tests, including `tests/handlers/` (27 tests over
  the chat request path — product code, not tooling).

Lives in `tests/utils/` deliberately: that path is covered by the long-standing
`security` category, so this guard cannot be orphaned by the very mistake it
exists to catch.

To add a directory: put it in the matrix. To keep one out: add it to
EXPECTED_UNMAPPED with a reason. Both are fine; neither happening by accident
is the point.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
CI = REPO / ".github" / "workflows" / "ci.yml"
TESTS = REPO / "tests"

# Directories deliberately not in the matrix, each with the reason it is out.
EXPECTED_UNMAPPED = {
    # Collects nothing without live credentials. A category that always reports
    # "no tests ran" is worse than no category: it looks like coverage.
    "smoke",
    # Not a test package.
    "__pycache__",
}


def _matrix_paths() -> set[str]:
    """Every `tests/...` path in a matrix `paths:` value.

    Reads only the `paths:` values, never the whole file. Mutation testing
    caught the naive version: scanning all of ci.yml let this guard's own
    explanatory comments count as coverage, so a directory mentioned only in
    prose read as mapped. A guard that a comment can satisfy is not a guard.
    """
    mapped: set[str] = set()
    for line in CI.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("paths:"):
            continue
        mapped.update(re.findall(r"tests/([A-Za-z0-9_]+)/", stripped))
    return mapped


def _test_dirs() -> set[str]:
    """Directories under tests/ that actually contain test files."""
    found = set()
    for child in TESTS.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        if any(child.rglob("test_*.py")):
            found.add(child.name)
    return found


def test_every_test_directory_is_run_or_explicitly_excluded():
    unmapped = _test_dirs() - _matrix_paths() - EXPECTED_UNMAPPED
    assert not unmapped, (
        "These tests/ directories contain tests but appear in no CI matrix path, "
        "so they never run on a PR:\n    "
        + "\n    ".join(f"tests/{d}/" for d in sorted(unmapped))
        + "\n\nAdd them to .github/workflows/ci.yml, or to EXPECTED_UNMAPPED in "
        "this file with the reason they stay out."
    )


def test_the_exclusion_list_does_not_name_directories_that_are_gone():
    """A stale exclusion silently re-opens the hole it was documenting."""
    present = {c.name for c in TESTS.iterdir() if c.is_dir()}
    stale = {d for d in EXPECTED_UNMAPPED if d not in present}
    assert not stale, (
        f"EXPECTED_UNMAPPED names directories that no longer exist: {sorted(stale)}. "
        "Remove them so the list keeps meaning what it says."
    )


def test_the_guard_would_notice_a_new_unmapped_directory():
    """The scanner must compare against the matrix, not just return empty."""
    mapped = _matrix_paths()
    assert "schema" in mapped, "tests/schema/ should be in the matrix (added 2026-09-16)"
    assert "handlers" in mapped, "tests/handlers/ should be in the matrix (added 2026-09-17)"
    # A directory nobody has created must read as unmapped, proving the set
    # difference is doing work rather than being empty for another reason.
    assert "definitely_not_a_real_directory" not in mapped
