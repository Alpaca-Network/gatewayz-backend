"""
Update stats in wiki pages that reference auto-generated numbers.

Updates the Testing Guide and Home page with current counts:
- Total test files, test functions
- Total route files, endpoints
- Last updated timestamps
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WIKI_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else None


def count_tests() -> dict:
    """Count test files and test functions."""
    import ast

    tests_dir = REPO_ROOT / "tests"
    if not tests_dir.exists():
        return {"files": 0, "functions": 0, "classes": 0}

    files = 0
    functions = 0
    classes = 0

    for filepath in tests_dir.rglob("*.py"):
        if filepath.name.startswith("__") or "conftest" in filepath.name:
            continue

        try:
            source = filepath.read_text()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue

        has_tests = False

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    functions += 1
                    has_tests = True
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                classes += 1

        if has_tests:
            files += 1

    return {"files": files, "functions": functions, "classes": classes}


def count_endpoints() -> dict:
    """Count route files and endpoints."""
    import ast

    routes_dir = REPO_ROOT / "src" / "routes"
    if not routes_dir.exists():
        return {"files": 0, "endpoints": 0}

    files = 0
    endpoints = 0

    for filepath in sorted(routes_dir.glob("*.py")):
        if filepath.name.startswith("__"):
            continue

        try:
            source = filepath.read_text()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue

        file_endpoints = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    if node.func.value.id in ("router", "sentry_tunnel_router"):
                        if node.func.attr in ("get", "post", "put", "delete", "patch"):
                            file_endpoints += 1

        if file_endpoints > 0:
            files += 1
            endpoints += file_endpoints

    return {"files": files, "endpoints": endpoints}


def count_services() -> int:
    """Count service modules."""
    services_dir = REPO_ROOT / "src" / "services"
    if not services_dir.exists():
        return 0
    return len([f for f in services_dir.glob("*.py") if not f.name.startswith("__")])


def count_db_modules() -> int:
    """Count database modules."""
    db_dir = REPO_ROOT / "src" / "db"
    if not db_dir.exists():
        return 0
    return len([f for f in db_dir.glob("*.py") if not f.name.startswith("__")])


def count_migrations() -> int:
    """Count Supabase migrations."""
    migrations_dir = REPO_ROOT / "supabase" / "migrations"
    if not migrations_dir.exists():
        return 0
    return len(list(migrations_dir.glob("*.sql")))


def count_loc() -> int:
    """Count lines of Python code in src/."""
    src_dir = REPO_ROOT / "src"
    if not src_dir.exists():
        return 0
    total = 0
    for f in src_dir.rglob("*.py"):
        try:
            total += len(f.read_text().splitlines())
        except (UnicodeDecodeError, OSError):
            pass
    return total


def main():
    if not WIKI_DIR:
        print("Usage: python generate_wiki_stats.py <wiki_directory>", file=sys.stderr)
        sys.exit(1)

    if not WIKI_DIR.exists():
        print(f"Error: Wiki directory not found: {WIKI_DIR}", file=sys.stderr)
        sys.exit(1)

    # Gather stats
    test_stats = count_tests()
    endpoint_stats = count_endpoints()
    services = count_services()
    db_modules = count_db_modules()
    migrations = count_migrations()
    loc = count_loc()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    stats = {
        "test_files": test_stats["files"],
        "test_functions": test_stats["functions"],
        "test_classes": test_stats["classes"],
        "route_files": endpoint_stats["files"],
        "endpoints": endpoint_stats["endpoints"],
        "services": services,
        "db_modules": db_modules,
        "migrations": migrations,
        "loc": loc,
        "date": now,
    }

    print(f"Stats collected:", file=sys.stderr)
    for k, v in stats.items():
        print(f"  {k}: {v}", file=sys.stderr)

    # Write stats to a file the workflow can source
    stats_file = WIKI_DIR / ".wiki-stats.txt"
    with open(stats_file, "w") as f:
        for k, v in stats.items():
            f.write(f"{k}={v}\n")

    print(f"Stats written to {stats_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
