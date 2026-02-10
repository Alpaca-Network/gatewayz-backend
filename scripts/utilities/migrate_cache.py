#!/usr/bin/env python3
"""
Cache Migration Script: cache.py → model_catalog_cache.py

This script helps migrate from the deprecated cache.py to the new Redis-based
model_catalog_cache.py system.

Usage:
    # Dry run (shows what would change)
    python scripts/utilities/migrate_cache.py --dry-run

    # Migrate specific file
    python scripts/utilities/migrate_cache.py src/services/openrouter_client.py

    # Migrate all provider clients
    python scripts/utilities/migrate_cache.py --all-providers

    # Generate migration report
    python scripts/utilities/migrate_cache.py --report
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Dict, Tuple


# Migration patterns
MIGRATION_PATTERNS = [
    # Pattern 1: Direct cache dictionary imports
    {
        "old": r"from src\.cache import (_\w+_models_cache)",
        "new": lambda m: "# MIGRATED: Use get_cached_gateway_catalog() instead of direct cache access",
        "description": "Direct cache dictionary import",
    },
    # Pattern 2: Error state functions
    {
        "old": r"from src\.cache import (.*(clear_gateway_error|set_gateway_error|is_gateway_in_error_state).*)",
        "new": lambda m: "# MIGRATED: Error state handling now built into cache with circuit breaker",
        "description": "Error state functions",
    },
    # Pattern 3: Cache clearing
    {
        "old": r"from src\.cache import clear_models_cache",
        "new": "from src.services.model_catalog_cache import invalidate_provider_catalog",
        "description": "Cache clearing function",
    },
    # Pattern 4: Cache access functions
    {
        "old": r"from src\.cache import (get_models_cache|get_modelz_cache)",
        "new": "from src.services.model_catalog_cache import get_cached_gateway_catalog",
        "description": "Cache access functions",
    },
]

# Provider-specific cache variable names
PROVIDER_CACHES = {
    "_models_cache": "openrouter",
    "_featherless_models_cache": "featherless",
    "_deepinfra_models_cache": "deepinfra",
    "_chutes_models_cache": "chutes",
    "_groq_models_cache": "groq",
    "_fireworks_models_cache": "fireworks",
    "_together_models_cache": "together",
    "_google_vertex_models_cache": "google-vertex",
    "_cerebras_models_cache": "cerebras",
    "_nebius_models_cache": "nebius",
    "_xai_models_cache": "xai",
    "_zai_models_cache": "zai",
    "_novita_models_cache": "novita",
    "_huggingface_models_cache": "huggingface",
    "_aimo_models_cache": "aimo",
    "_near_models_cache": "near",
    "_fal_models_cache": "fal",
    "_vercel_ai_gateway_models_cache": "vercel-ai-gateway",
    "_helicone_models_cache": "helicone",
    "_aihubmix_models_cache": "aihubmix",
    "_anannas_models_cache": "anannas",
    "_alibaba_models_cache": "alibaba",
    "_onerouter_models_cache": "onerouter",
    "_cloudflare_workers_ai_models_cache": "cloudflare-workers-ai",
    "_clarifai_models_cache": "clarifai",
    "_openai_models_cache": "openai",
    "_anthropic_models_cache": "anthropic",
    "_simplismart_models_cache": "simplismart",
    "_sybil_models_cache": "sybil",
    "_canopywave_models_cache": "canopywave",
    "_morpheus_models_cache": "morpheus",
    "_modelz_cache": "modelz",
}


def find_provider_from_cache_var(cache_var: str) -> str:
    """Get provider slug from cache variable name"""
    return PROVIDER_CACHES.get(cache_var, cache_var.replace("_", "-").strip("-"))


def analyze_file(file_path: Path) -> Dict:
    """Analyze a file for cache.py usage"""
    if not file_path.exists():
        return {"error": "File not found"}

    content = file_path.read_text()
    issues = []

    # Find all cache.py imports
    import_pattern = r"from src\.cache import ([^\n]+)"
    imports = re.findall(import_pattern, content)

    for imp in imports:
        issues.append({
            "type": "import",
            "line": content[:content.find(imp)].count("\n") + 1,
            "content": f"from src.cache import {imp}",
            "severity": "high",
        })

    # Find direct cache dictionary access
    for cache_var, provider in PROVIDER_CACHES.items():
        if cache_var in content:
            pattern = rf'{cache_var}\["data"\]'
            matches = re.finditer(pattern, content)
            for match in matches:
                line_num = content[:match.start()].count("\n") + 1
                issues.append({
                    "type": "cache_access",
                    "line": line_num,
                    "content": match.group(0),
                    "provider": provider,
                    "severity": "high",
                })

    # Find error state function calls
    error_funcs = ["is_gateway_in_error_state", "set_gateway_error", "clear_gateway_error"]
    for func in error_funcs:
        if func in content:
            pattern = rf'{func}\(["\'](\w+)["\']\)'
            matches = re.finditer(pattern, content)
            for match in matches:
                line_num = content[:match.start()].count("\n") + 1
                issues.append({
                    "type": "error_func",
                    "line": line_num,
                    "content": match.group(0),
                    "function": func,
                    "provider": match.group(1),
                    "severity": "medium",
                })

    # Find clear_models_cache calls
    if "clear_models_cache" in content:
        pattern = r'clear_models_cache\(["\']?(\w+)["\']?\)'
        matches = re.finditer(pattern, content)
        for match in matches:
            line_num = content[:match.start()].count("\n") + 1
            issues.append({
                "type": "cache_clear",
                "line": line_num,
                "content": match.group(0),
                "provider": match.group(1) if match.group(1) else "unknown",
                "severity": "medium",
            })

    return {
        "file": str(file_path),
        "issues": issues,
        "needs_migration": len(issues) > 0,
    }


def generate_migration_suggestions(file_path: Path, issues: List[Dict]) -> List[str]:
    """Generate specific migration suggestions for a file"""
    suggestions = []

    # Group issues by type
    by_type = {}
    for issue in issues:
        issue_type = issue["type"]
        if issue_type not in by_type:
            by_type[issue_type] = []
        by_type[issue_type].append(issue)

    # Import replacements
    if "import" in by_type:
        suggestions.append("## Import Changes")
        suggestions.append("```python")
        suggestions.append("# Remove these imports:")
        for issue in by_type["import"]:
            suggestions.append(f"# {issue['content']}")
        suggestions.append("")
        suggestions.append("# Add these imports:")
        suggestions.append("from src.services.model_catalog_cache import (")
        suggestions.append("    get_cached_gateway_catalog,")
        suggestions.append("    set_cached_gateway_catalog,")
        suggestions.append("    invalidate_provider_catalog")
        suggestions.append(")")
        suggestions.append("```")
        suggestions.append("")

    # Cache access replacements
    if "cache_access" in by_type:
        suggestions.append("## Cache Access Changes")
        for issue in by_type["cache_access"]:
            provider = issue.get("provider", "unknown")
            suggestions.append(f"**Line {issue['line']}:** Replace `{issue['content']}`")
            suggestions.append("```python")
            suggestions.append(f"# OLD: {issue['content']}")
            suggestions.append(f"# NEW: get_cached_gateway_catalog('{provider}')")
            suggestions.append("```")
            suggestions.append("")

    # Error function replacements
    if "error_func" in by_type:
        suggestions.append("## Error Handling Changes")
        suggestions.append("Error state tracking is now handled automatically by the cache layer.")
        suggestions.append("```python")
        suggestions.append("# OLD: Manual error tracking")
        suggestions.append("if is_gateway_in_error_state('provider'):")
        suggestions.append("    return []")
        suggestions.append("try:")
        suggestions.append("    models = fetch_from_api()")
        suggestions.append("    clear_gateway_error('provider')")
        suggestions.append("except Exception as e:")
        suggestions.append("    set_gateway_error('provider', str(e))")
        suggestions.append("")
        suggestions.append("# NEW: Circuit breaker handles this automatically")
        suggestions.append("cached = get_cached_gateway_catalog('provider')")
        suggestions.append("if cached:")
        suggestions.append("    return cached")
        suggestions.append("try:")
        suggestions.append("    models = fetch_from_api()")
        suggestions.append("    set_cached_gateway_catalog('provider', models)")
        suggestions.append("    return models")
        suggestions.append("except Exception as e:")
        suggestions.append("    logger.error(f'Failed to fetch: {e}')")
        suggestions.append("    return []")
        suggestions.append("```")
        suggestions.append("")

    # Cache clear replacements
    if "cache_clear" in by_type:
        suggestions.append("## Cache Invalidation Changes")
        for issue in by_type["cache_clear"]:
            provider = issue.get("provider", "unknown")
            suggestions.append(f"**Line {issue['line']}:** Replace `{issue['content']}`")
            suggestions.append("```python")
            suggestions.append(f"# OLD: clear_models_cache('{provider}')")
            suggestions.append(f"# NEW: invalidate_provider_catalog('{provider}')")
            suggestions.append("```")
            suggestions.append("")

    return suggestions


def generate_report(files: List[Path]) -> str:
    """Generate a comprehensive migration report"""
    report = ["# Cache Migration Report", ""]

    all_analyses = []
    total_issues = 0

    for file_path in files:
        analysis = analyze_file(file_path)
        if analysis.get("needs_migration"):
            all_analyses.append(analysis)
            total_issues += len(analysis["issues"])

    report.append(f"**Total files analyzed:** {len(files)}")
    report.append(f"**Files needing migration:** {len(all_analyses)}")
    report.append(f"**Total issues found:** {total_issues}")
    report.append("")

    # Summary by file
    report.append("## Files Needing Migration")
    report.append("")
    for analysis in all_analyses:
        issues = analysis["issues"]
        high = sum(1 for i in issues if i["severity"] == "high")
        medium = sum(1 for i in issues if i["severity"] == "medium")
        report.append(f"- **{analysis['file']}** - {len(issues)} issues (🔴 {high} high, 🟡 {medium} medium)")

    report.append("")

    # Priority order
    report.append("## Suggested Migration Order")
    report.append("")

    # Sort by number of high-severity issues
    priority = sorted(all_analyses, key=lambda x: sum(1 for i in x["issues"] if i["severity"] == "high"), reverse=True)

    report.append("### High Priority (High Traffic)")
    high_priority = ["openrouter", "deepinfra", "featherless", "fireworks", "together"]
    for analysis in priority[:5]:
        file_name = Path(analysis["file"]).name
        if any(p in analysis["file"] for p in high_priority):
            report.append(f"1. `{analysis['file']}` - {len(analysis['issues'])} issues")

    report.append("")
    report.append("### Medium Priority")
    for analysis in priority[5:15]:
        report.append(f"- `{analysis['file']}` - {len(analysis['issues'])} issues")

    report.append("")
    report.append("### Low Priority")
    for analysis in priority[15:]:
        report.append(f"- `{analysis['file']}` - {len(analysis['issues'])} issues")

    return "\n".join(report)


def main():
    parser = argparse.ArgumentParser(description="Cache migration helper")
    parser.add_argument("files", nargs="*", help="Files to analyze/migrate")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    parser.add_argument("--report", action="store_true", help="Generate migration report")
    parser.add_argument("--all-providers", action="store_true", help="Analyze all provider clients")

    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent.parent

    # Determine files to process
    if args.all_providers:
        files = list((repo_root / "src" / "services").glob("*_client.py"))
    elif args.files:
        files = [Path(f) for f in args.files]
    else:
        # Default: scan all src files
        files = list((repo_root / "src").rglob("*.py"))

    # Generate report
    if args.report:
        report = generate_report(files)
        print(report)

        # Save to file
        report_path = repo_root / "docs" / "CACHE_MIGRATION_REPORT.md"
        report_path.write_text(report)
        print(f"\n✅ Report saved to: {report_path}")
        return

    # Analyze specific files
    for file_path in files:
        analysis = analyze_file(file_path)

        if analysis.get("error"):
            print(f"❌ Error: {analysis['error']}")
            continue

        if not analysis["needs_migration"]:
            print(f"✅ {file_path} - No migration needed")
            continue

        print(f"\n{'='*80}")
        print(f"📄 File: {analysis['file']}")
        print(f"{'='*80}")
        print(f"Issues found: {len(analysis['issues'])}")

        # Show issues
        for issue in analysis["issues"]:
            severity_emoji = "🔴" if issue["severity"] == "high" else "🟡"
            print(f"\n{severity_emoji} Line {issue['line']}: {issue['type']}")
            print(f"   {issue['content']}")

        # Show suggestions
        print("\n## Migration Suggestions")
        print("="*80)
        suggestions = generate_migration_suggestions(file_path, analysis["issues"])
        print("\n".join(suggestions))

    print("\n" + "="*80)
    print("Migration analysis complete!")
    print(f"Run with --report to generate full migration report")
    print("="*80)


if __name__ == "__main__":
    main()
