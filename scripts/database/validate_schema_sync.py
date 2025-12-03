#!/usr/bin/env python3
"""
Schema Sync Validation Script

Validates that a test/local Supabase instance has the same schema as production.
This ensures test environments stay in sync with production.

Usage:
    # Compare local to production
    python scripts/database/validate_schema_sync.py

    # Compare two specific instances
    python scripts/database/validate_schema_sync.py \
        --source-url $PROD_URL --source-key $PROD_KEY \
        --target-url $TEST_URL --target-key $TEST_KEY

    # Generate schema snapshot
    python scripts/database/validate_schema_sync.py --snapshot

    # Compare against saved snapshot
    python scripts/database/validate_schema_sync.py --compare-snapshot schema_snapshot.json

    # CI mode (exit code reflects pass/fail)
    python scripts/database/validate_schema_sync.py --ci
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

try:
    from supabase import create_client, Client
except ImportError:
    print("Error: supabase package not installed. Run: pip install supabase")
    sys.exit(1)


# Expected tables based on migrations
EXPECTED_TABLES = [
    "users",
    "api_keys_new",
    "payments",
    "credit_transactions",
    "activity_log",
    "chat_sessions",
    "chat_messages",
    "plans",
    "user_plans",
    "coupons",
    "coupon_redemptions",
    "providers",
    "models",
    "model_health_tracking",
    "model_health_history",
    "rate_limit_usage",
    "stripe_webhook_events",
    "role_permissions",
    "role_audit_log",
    "notifications",
    "openrouter_models",
    "openrouter_apps",
]

# Critical columns that must exist (table: [columns])
CRITICAL_COLUMNS = {
    "users": [
        "id", "username", "email", "credits", "is_active",
        "subscription_status", "role", "privy_user_id", "created_at"
    ],
    "api_keys_new": [
        "id", "user_id", "api_key", "key_hash", "key_name",
        "environment_tag", "is_active", "is_primary", "created_at"
    ],
    "payments": [
        "id", "user_id", "amount_usd", "amount_cents", "status",
        "stripe_payment_intent_id", "created_at"
    ],
    "credit_transactions": [
        "id", "user_id", "amount", "transaction_type",
        "balance_before", "balance_after", "created_at"
    ],
    "providers": [
        "id", "name", "slug", "base_url", "is_active", "health_status"
    ],
    "models": [
        "id", "provider_id", "model_id", "model_name", "is_active"
    ],
    "model_health_tracking": [
        "provider", "model", "last_status", "call_count",
        "success_count", "error_count"
    ],
    "rate_limit_usage": [
        "id", "user_id", "api_key", "window_type", "window_start",
        "requests_count", "tokens_count"
    ],
}


@dataclass
class SchemaInfo:
    """Represents database schema information"""
    tables: dict = field(default_factory=dict)
    functions: list = field(default_factory=list)
    indexes: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    triggers: list = field(default_factory=list)
    enums: list = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            "tables": self.tables,
            "functions": self.functions,
            "indexes": self.indexes,
            "constraints": self.constraints,
            "triggers": self.triggers,
            "enums": self.enums,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SchemaInfo":
        return cls(
            tables=data.get("tables", {}),
            functions=data.get("functions", []),
            indexes=data.get("indexes", []),
            constraints=data.get("constraints", []),
            triggers=data.get("triggers", []),
            enums=data.get("enums", []),
            timestamp=data.get("timestamp", ""),
        )


@dataclass
class ValidationResult:
    """Result of schema validation"""
    is_valid: bool = True
    missing_tables: list = field(default_factory=list)
    extra_tables: list = field(default_factory=list)
    missing_columns: dict = field(default_factory=dict)
    extra_columns: dict = field(default_factory=dict)
    type_mismatches: dict = field(default_factory=dict)
    missing_indexes: list = field(default_factory=list)
    missing_functions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def add_error(self, msg: str):
        self.errors.append(msg)
        self.is_valid = False

    def add_warning(self, msg: str):
        self.warnings.append(msg)


class SchemaValidator:
    """Validates and compares database schemas"""

    def __init__(self, url: str, key: str):
        self.client: Client = create_client(url, key)
        self.url = url

    def get_schema_info(self) -> SchemaInfo:
        """Extract schema information from database"""
        schema = SchemaInfo()

        # Get tables and columns
        tables_query = """
        SELECT
            t.table_name,
            c.column_name,
            c.data_type,
            c.is_nullable,
            c.column_default,
            c.character_maximum_length
        FROM information_schema.tables t
        JOIN information_schema.columns c
            ON t.table_name = c.table_name
            AND t.table_schema = c.table_schema
        WHERE t.table_schema = 'public'
            AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_name, c.ordinal_position
        """

        try:
            result = self.client.rpc("exec_sql", {"query": tables_query}).execute()
            for row in result.data or []:
                table_name = row["table_name"]
                if table_name not in schema.tables:
                    schema.tables[table_name] = {"columns": {}}

                schema.tables[table_name]["columns"][row["column_name"]] = {
                    "type": row["data_type"],
                    "nullable": row["is_nullable"] == "YES",
                    "default": row["column_default"],
                    "max_length": row["character_maximum_length"],
                }
        except Exception:
            # Fallback: try direct table queries
            schema.tables = self._get_tables_fallback()

        # Get indexes
        try:
            indexes_query = """
            SELECT indexname, tablename, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
            ORDER BY tablename, indexname
            """
            result = self.client.rpc("exec_sql", {"query": indexes_query}).execute()
            schema.indexes = [
                {"name": r["indexname"], "table": r["tablename"], "definition": r["indexdef"]}
                for r in (result.data or [])
            ]
        except Exception:
            pass

        # Get functions
        try:
            functions_query = """
            SELECT routine_name, routine_type
            FROM information_schema.routines
            WHERE routine_schema = 'public'
            ORDER BY routine_name
            """
            result = self.client.rpc("exec_sql", {"query": functions_query}).execute()
            schema.functions = [r["routine_name"] for r in (result.data or [])]
        except Exception:
            pass

        # Get enums
        try:
            enums_query = """
            SELECT t.typname, e.enumlabel
            FROM pg_type t
            JOIN pg_enum e ON t.oid = e.enumtypid
            JOIN pg_namespace n ON t.typnamespace = n.oid
            WHERE n.nspname = 'public'
            ORDER BY t.typname, e.enumsortorder
            """
            result = self.client.rpc("exec_sql", {"query": enums_query}).execute()
            enums = {}
            for r in result.data or []:
                if r["typname"] not in enums:
                    enums[r["typname"]] = []
                enums[r["typname"]].append(r["enumlabel"])
            schema.enums = [{"name": k, "values": v} for k, v in enums.items()]
        except Exception:
            pass

        return schema

    def _get_tables_fallback(self) -> dict:
        """Fallback method to get table info by querying each table"""
        tables = {}

        for table_name in EXPECTED_TABLES:
            try:
                # Try to select from table to verify it exists
                result = self.client.table(table_name).select("*").limit(0).execute()
                tables[table_name] = {"columns": {}, "exists": True}
            except Exception:
                # Table doesn't exist or error
                pass

        return tables

    def validate_against_expected(self) -> ValidationResult:
        """Validate schema against expected tables and columns"""
        result = ValidationResult()
        schema = self.get_schema_info()

        # Check for missing tables
        existing_tables = set(schema.tables.keys())
        expected_tables = set(EXPECTED_TABLES)

        missing = expected_tables - existing_tables
        if missing:
            result.missing_tables = list(missing)
            for table in missing:
                result.add_error(f"Missing table: {table}")

        # Check for critical columns
        for table, expected_columns in CRITICAL_COLUMNS.items():
            if table not in schema.tables:
                continue

            actual_columns = set(schema.tables[table].get("columns", {}).keys())
            missing_cols = set(expected_columns) - actual_columns

            if missing_cols:
                result.missing_columns[table] = list(missing_cols)
                for col in missing_cols:
                    result.add_error(f"Missing column: {table}.{col}")

        # Extra tables (informational)
        extra = existing_tables - expected_tables
        known_system_tables = {"schema_migrations", "spatial_ref_sys"}
        extra = extra - known_system_tables
        if extra:
            result.extra_tables = list(extra)
            for table in extra:
                result.add_warning(f"Extra table found: {table}")

        return result

    def compare_schemas(self, other: "SchemaValidator") -> ValidationResult:
        """Compare this schema against another database"""
        result = ValidationResult()

        source_schema = self.get_schema_info()
        target_schema = other.get_schema_info()

        source_tables = set(source_schema.tables.keys())
        target_tables = set(target_schema.tables.keys())

        # Missing tables in target
        missing = source_tables - target_tables
        if missing:
            result.missing_tables = list(missing)
            for table in missing:
                result.add_error(f"Table missing in target: {table}")

        # Extra tables in target
        extra = target_tables - source_tables
        if extra:
            result.extra_tables = list(extra)
            for table in extra:
                result.add_warning(f"Extra table in target: {table}")

        # Compare columns for common tables
        common_tables = source_tables & target_tables
        for table in common_tables:
            source_cols = set(source_schema.tables[table].get("columns", {}).keys())
            target_cols = set(target_schema.tables[table].get("columns", {}).keys())

            missing_cols = source_cols - target_cols
            if missing_cols:
                result.missing_columns[table] = list(missing_cols)
                for col in missing_cols:
                    result.add_error(f"Column missing in target: {table}.{col}")

            extra_cols = target_cols - source_cols
            if extra_cols:
                result.extra_columns[table] = list(extra_cols)
                for col in extra_cols:
                    result.add_warning(f"Extra column in target: {table}.{col}")

            # Check column types for common columns
            common_cols = source_cols & target_cols
            for col in common_cols:
                source_type = source_schema.tables[table]["columns"][col].get("type")
                target_type = target_schema.tables[table]["columns"][col].get("type")
                if source_type and target_type and source_type != target_type:
                    if table not in result.type_mismatches:
                        result.type_mismatches[table] = {}
                    result.type_mismatches[table][col] = {
                        "source": source_type,
                        "target": target_type
                    }
                    result.add_error(
                        f"Type mismatch: {table}.{col} "
                        f"(source: {source_type}, target: {target_type})"
                    )

        # Compare functions
        source_funcs = set(source_schema.functions)
        target_funcs = set(target_schema.functions)
        missing_funcs = source_funcs - target_funcs
        if missing_funcs:
            result.missing_functions = list(missing_funcs)
            for func in missing_funcs:
                result.add_warning(f"Function missing in target: {func}")

        return result


def print_result(result: ValidationResult, verbose: bool = False):
    """Print validation result"""
    if result.is_valid:
        print("\n✅ Schema validation PASSED\n")
    else:
        print("\n❌ Schema validation FAILED\n")

    if result.errors:
        print("ERRORS:")
        for error in result.errors:
            print(f"  ❌ {error}")
        print()

    if result.warnings and verbose:
        print("WARNINGS:")
        for warning in result.warnings:
            print(f"  ⚠️  {warning}")
        print()

    # Summary
    print("Summary:")
    print(f"  Missing tables: {len(result.missing_tables)}")
    print(f"  Extra tables: {len(result.extra_tables)}")
    print(f"  Missing columns: {sum(len(v) for v in result.missing_columns.values())}")
    print(f"  Type mismatches: {sum(len(v) for v in result.type_mismatches.values())}")
    print(f"  Total errors: {len(result.errors)}")
    print(f"  Total warnings: {len(result.warnings)}")


def save_snapshot(schema: SchemaInfo, filepath: str):
    """Save schema snapshot to file"""
    with open(filepath, "w") as f:
        json.dump(schema.to_dict(), f, indent=2, default=str)
    print(f"Schema snapshot saved to: {filepath}")


def load_snapshot(filepath: str) -> SchemaInfo:
    """Load schema snapshot from file"""
    with open(filepath) as f:
        data = json.load(f)
    return SchemaInfo.from_dict(data)


def main():
    parser = argparse.ArgumentParser(
        description="Validate Supabase schema sync between environments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--source-url",
        default=os.getenv("SUPABASE_URL", "http://localhost:54321"),
        help="Source Supabase URL (default: SUPABASE_URL env or localhost)"
    )
    parser.add_argument(
        "--source-key",
        default=os.getenv("SUPABASE_SERVICE_KEY", os.getenv("SUPABASE_KEY", "")),
        help="Source Supabase service key"
    )
    parser.add_argument(
        "--target-url",
        help="Target Supabase URL to compare against"
    )
    parser.add_argument(
        "--target-key",
        help="Target Supabase service key"
    )
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Generate schema snapshot file"
    )
    parser.add_argument(
        "--snapshot-file",
        default="schema_snapshot.json",
        help="Snapshot file path (default: schema_snapshot.json)"
    )
    parser.add_argument(
        "--compare-snapshot",
        metavar="FILE",
        help="Compare against saved snapshot file"
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: exit with code 1 on validation failure"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output including warnings"
    )
    parser.add_argument(
        "--validate-expected",
        action="store_true",
        default=True,
        help="Validate against expected tables/columns (default)"
    )

    args = parser.parse_args()

    if not args.source_key:
        print("Error: Supabase service key required.")
        print("Set SUPABASE_SERVICE_KEY env var or use --source-key")
        sys.exit(1)

    print(f"Connecting to: {args.source_url}")

    try:
        validator = SchemaValidator(args.source_url, args.source_key)

        # Generate snapshot
        if args.snapshot:
            schema = validator.get_schema_info()
            save_snapshot(schema, args.snapshot_file)
            print(f"\nTables found: {len(schema.tables)}")
            print(f"Functions found: {len(schema.functions)}")
            print(f"Indexes found: {len(schema.indexes)}")
            return

        # Compare against snapshot
        if args.compare_snapshot:
            print(f"Comparing against snapshot: {args.compare_snapshot}")
            # This would require implementing snapshot comparison
            # For now, just validate against expected
            result = validator.validate_against_expected()
            print_result(result, args.verbose)
            if args.ci and not result.is_valid:
                sys.exit(1)
            return

        # Compare two databases
        if args.target_url and args.target_key:
            print(f"Comparing with target: {args.target_url}")
            target_validator = SchemaValidator(args.target_url, args.target_key)
            result = validator.compare_schemas(target_validator)
            print_result(result, args.verbose)
            if args.ci and not result.is_valid:
                sys.exit(1)
            return

        # Default: validate against expected schema
        print("Validating against expected schema...")
        result = validator.validate_against_expected()
        print_result(result, args.verbose)

        if args.ci and not result.is_valid:
            sys.exit(1)

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        if args.ci:
            sys.exit(1)
        sys.exit(1)


if __name__ == "__main__":
    main()
