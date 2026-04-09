"""
Generate Test-Mapping.md wiki page by scanning all test files.

Walks tests/, extracts test classes and test functions using AST parsing,
and generates a markdown page with counts and organization.
"""

import ast
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TESTS_DIR = REPO_ROOT / "tests"


def extract_tests_from_file(filepath: Path) -> dict:
    """Extract test classes and test functions from a Python test file."""
    result = {
        "classes": [],
        "standalone_functions": [],
    }

    try:
        source = filepath.read_text()
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return result

    for node in ast.iter_child_nodes(tree):
        # Standalone test functions
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                docstring = ast.get_docstring(node) or ""
                if docstring:
                    docstring = docstring.split("\n")[0].strip()
                result["standalone_functions"].append({
                    "name": node.name,
                    "line": node.lineno,
                    "docstring": docstring,
                })

        # Test classes
        if isinstance(node, ast.ClassDef):
            if node.name.startswith("Test"):
                methods = []
                for item in ast.iter_child_nodes(node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if item.name.startswith("test_"):
                            docstring = ast.get_docstring(item) or ""
                            if docstring:
                                docstring = docstring.split("\n")[0].strip()
                            methods.append({
                                "name": item.name,
                                "line": item.lineno,
                                "docstring": docstring,
                            })

                result["classes"].append({
                    "name": node.name,
                    "line": node.lineno,
                    "methods": methods,
                })

    return result


def get_relative_path(filepath: Path) -> str:
    """Get path relative to repo root."""
    try:
        return str(filepath.relative_to(REPO_ROOT))
    except ValueError:
        return str(filepath)


def generate_markdown(all_tests: dict[str, dict]) -> str:
    """Generate the complete Test-Mapping.md content."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Compute totals
    total_files = len(all_tests)
    total_classes = sum(
        len(data["classes"]) for data in all_tests.values()
    )
    total_functions = sum(
        len(data["standalone_functions"]) +
        sum(len(cls["methods"]) for cls in data["classes"])
        for data in all_tests.values()
    )

    # Group by directory
    by_directory: dict[str, list[tuple[str, dict]]] = {}
    for filepath, data in sorted(all_tests.items()):
        parts = Path(filepath).parts
        # Use first 2 levels under tests/ as the group key
        if len(parts) >= 2:
            group = "/".join(parts[:2])
        else:
            group = parts[0] if parts else "tests"
        by_directory.setdefault(group, []).append((filepath, data))

    lines = [
        "# Test Mapping",
        "",
        "> Auto-generated from test files in `tests/`. Do not edit manually.",
        ">",
        f"> **Generated**: {now} | **Source**: `scripts/wiki/generate_test_mapping.py`",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Test files | {total_files} |",
        f"| Test classes | {total_classes} |",
        f"| Test functions | {total_functions} |",
        f"| Test directories | {len(by_directory)} |",
        "",
        "---",
        "",
        "## By Directory",
        "",
    ]

    # Directory summary table
    lines.extend([
        "| Directory | Files | Classes | Functions |",
        "|-----------|-------|---------|-----------|",
    ])

    for group in sorted(by_directory.keys()):
        entries = by_directory[group]
        files = len(entries)
        classes = sum(len(d["classes"]) for _, d in entries)
        funcs = sum(
            len(d["standalone_functions"]) +
            sum(len(c["methods"]) for c in d["classes"])
            for _, d in entries
        )
        lines.append(f"| `{group}` | {files} | {classes} | {funcs} |")

    lines.extend(["", "---", ""])

    # Detailed per-directory sections
    for group in sorted(by_directory.keys()):
        entries = by_directory[group]
        group_funcs = sum(
            len(d["standalone_functions"]) +
            sum(len(c["methods"]) for c in d["classes"])
            for _, d in entries
        )

        lines.extend([
            f"## `{group}/`",
            "",
            f"**{len(entries)} files, {group_funcs} tests**",
            "",
        ])

        for filepath, data in sorted(entries):
            func_count = len(data["standalone_functions"]) + sum(
                len(c["methods"]) for c in data["classes"]
            )

            lines.extend([
                f"### `{filepath}`",
                "",
                f"**{func_count} tests**",
                "",
            ])

            # Standalone functions
            if data["standalone_functions"]:
                for func in data["standalone_functions"]:
                    desc = f" — {func['docstring']}" if func["docstring"] else ""
                    lines.append(f"- `{func['name']}`{desc}")

            # Classes
            for cls in data["classes"]:
                lines.append(f"- **{cls['name']}** ({len(cls['methods'])} tests)")
                for method in cls["methods"]:
                    desc = f" — {method['docstring']}" if method["docstring"] else ""
                    lines.append(f"  - `{method['name']}`{desc}")

            lines.append("")

        lines.extend(["---", ""])

    return "\n".join(lines)


def main():
    if not TESTS_DIR.exists():
        print(f"Error: Tests directory not found: {TESTS_DIR}", file=sys.stderr)
        sys.exit(1)

    all_tests: dict[str, dict] = {}

    for filepath in sorted(TESTS_DIR.rglob("*.py")):
        if filepath.name.startswith("__"):
            continue
        if "conftest" in filepath.name:
            continue

        data = extract_tests_from_file(filepath)

        # Only include files that have actual tests
        has_tests = bool(data["standalone_functions"]) or any(
            cls["methods"] for cls in data["classes"]
        )

        if has_tests:
            rel_path = get_relative_path(filepath)
            all_tests[rel_path] = data

    markdown = generate_markdown(all_tests)
    print(markdown)

    total_funcs = sum(
        len(d["standalone_functions"]) +
        sum(len(c["methods"]) for c in d["classes"])
        for d in all_tests.values()
    )
    print(f"Generated test mapping: {len(all_tests)} files, {total_funcs} tests", file=sys.stderr)


if __name__ == "__main__":
    main()
