"""
Generate API-Mappings.md wiki page by scanning all FastAPI route files.

Extracts every @router.{method}("/path") decorator, applies v1 prefix logic,
and generates a markdown page grouped by route file (system area).
"""

import ast
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ROUTES_DIR = REPO_ROOT / "src" / "routes"
MAIN_PY = REPO_ROOT / "src" / "main.py"

# V1 routes get /v1 prefix (extracted from main.py pattern)
V1_ROUTE_MODULES = {
    "chat", "detailed_status", "messages", "images", "audio",
    "tools", "catalog", "model_health", "status_page",
}


def extract_v1_modules_from_main() -> set[str]:
    """Try to extract v1 route module names from main.py dynamically."""
    if not MAIN_PY.exists():
        return V1_ROUTE_MODULES

    content = MAIN_PY.read_text()
    modules = set()

    # Look for v1_routes_to_load patterns like ("chat", "Chat Endpoints")
    pattern = re.compile(r'v1_routes_to_load.*?\[(.+?)\]', re.DOTALL)
    match = pattern.search(content)
    if match:
        tuples = re.findall(r'\("(\w+)"', match.group(1))
        if tuples:
            modules = set(tuples)

    return modules if modules else V1_ROUTE_MODULES


def extract_endpoints_from_file(filepath: Path) -> list[dict]:
    """Extract all route endpoints from a Python file using AST parsing."""
    endpoints = []

    try:
        source = filepath.read_text()
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return endpoints

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) and not isinstance(node, ast.FunctionDef):
            continue

        # Find the decorator that defines the route
        for decorator in node.decorator_list:
            method = None
            path = None

            # @router.get("/path") pattern
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                attr = decorator.func
                if isinstance(attr.value, ast.Name) and attr.value.id in ("router", "sentry_tunnel_router"):
                    method = attr.attr.upper()
                    if decorator.args and isinstance(decorator.args[0], ast.Constant):
                        path = decorator.args[0].value

            if method and path:
                # Extract docstring
                docstring = ast.get_docstring(node) or ""
                if docstring:
                    docstring = docstring.split("\n")[0].strip()

                # Extract dependencies (look for Depends() in arguments)
                deps = []
                for arg in node.args.defaults:
                    if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
                        if arg.func.id == "Depends" and arg.args:
                            if isinstance(arg.args[0], ast.Name):
                                deps.append(arg.args[0].id)

                endpoints.append({
                    "method": method,
                    "path": path,
                    "function": node.name,
                    "docstring": docstring,
                    "line": node.lineno,
                    "deps": deps,
                })

    return endpoints


def module_name_from_path(filepath: Path) -> str:
    """Get module name from file path (e.g., 'chat' from 'chat.py')."""
    return filepath.stem


def display_name(module: str) -> str:
    """Convert module name to display name."""
    return module.replace("_", " ").title()


def generate_markdown(all_routes: dict[str, list[dict]], v1_modules: set[str]) -> str:
    """Generate the complete API-Mappings.md content."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = [
        "# API Mappings",
        "",
        "> Auto-generated from route files in `src/routes/`. Do not edit manually.",
        ">",
        f"> **Generated**: {now} | **Source**: `scripts/wiki/generate_api_mappings.py`",
        "",
        "---",
        "",
    ]

    # Summary
    total_endpoints = sum(len(eps) for eps in all_routes.values())
    total_files = len(all_routes)

    lines.extend([
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Route files | {total_files} |",
        f"| Total endpoints | {total_endpoints} |",
        f"| V1 endpoints (OpenAI/Anthropic compatible) | {sum(len(eps) for mod, eps in all_routes.items() if mod in v1_modules)} |",
        f"| Non-V1 endpoints | {sum(len(eps) for mod, eps in all_routes.items() if mod not in v1_modules)} |",
        "",
        "---",
        "",
        "## Table of Contents",
        "",
    ])

    for i, (module, eps) in enumerate(sorted(all_routes.items()), 1):
        prefix = "/v1" if module in v1_modules else ""
        lines.append(f"{i}. [{display_name(module)}](#{module.replace('_', '-')}) ({len(eps)} endpoints, prefix: `{prefix or '/'}`)")

    lines.extend(["", "---", ""])

    # Per-module sections
    for module in sorted(all_routes.keys()):
        eps = all_routes[module]
        prefix = "/v1" if module in v1_modules else ""

        lines.extend([
            f"## {display_name(module)}",
            "",
            f"**File**: `src/routes/{module}.py` | **Prefix**: `{prefix or '(root)'}` | **Endpoints**: {len(eps)}",
            "",
            "| Method | Path | Function | Description |",
            "|--------|------|----------|-------------|",
        ])

        for ep in sorted(eps, key=lambda e: e["path"]):
            full_path = f"{prefix}{ep['path']}"
            desc = ep["docstring"][:80] if ep["docstring"] else ""
            lines.append(f"| `{ep['method']}` | `{full_path}` | `{ep['function']}` | {desc} |")

        lines.extend(["", "---", ""])

    return "\n".join(lines)


def main():
    if not ROUTES_DIR.exists():
        print(f"Error: Routes directory not found: {ROUTES_DIR}", file=sys.stderr)
        sys.exit(1)

    v1_modules = extract_v1_modules_from_main()

    all_routes: dict[str, list[dict]] = {}

    for filepath in sorted(ROUTES_DIR.glob("*.py")):
        if filepath.name.startswith("__"):
            continue

        module = module_name_from_path(filepath)
        endpoints = extract_endpoints_from_file(filepath)

        if endpoints:
            all_routes[module] = endpoints

    markdown = generate_markdown(all_routes, v1_modules)

    # Output to stdout (workflow pipes to file)
    print(markdown)

    # Stats to stderr
    total = sum(len(eps) for eps in all_routes.values())
    print(f"Generated API mappings: {len(all_routes)} files, {total} endpoints", file=sys.stderr)


if __name__ == "__main__":
    main()
