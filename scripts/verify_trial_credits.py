#!/usr/bin/env python3
"""
Verification Script: Trial Credits Configuration
Validates that $5 trial credits with $1/day limit is configured correctly
"""

import os
import sys


def check_file_exists(filepath):
    """Check if a file exists"""
    return os.path.isfile(filepath)


def check_value_in_file(filepath, search_string):
    """Check if a string exists in a file"""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
            return search_string in content
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return False


def main():
    print("=" * 60)
    print("Trial Credits Configuration Verification")
    print("=" * 60)
    print()

    passed = 0
    failed = 0

    checks = [
        # Configuration file checks
        ("src/config/usage_limits.py exists",
         check_file_exists("src/config/usage_limits.py")),
        ("TRIAL_CREDITS_AMOUNT = 5.0 configured",
         check_value_in_file("src/config/usage_limits.py", "TRIAL_CREDITS_AMOUNT = 5.0")),
        ("TRIAL_DURATION_DAYS = 3 configured",
         check_value_in_file("src/config/usage_limits.py", "TRIAL_DURATION_DAYS = 3")),
        ("TRIAL_DAILY_LIMIT = 1.0 configured",
         check_value_in_file("src/config/usage_limits.py", "TRIAL_DAILY_LIMIT = 1.0")),
        ("DAILY_USAGE_LIMIT = 1.0 configured",
         check_value_in_file("src/config/usage_limits.py", "DAILY_USAGE_LIMIT = 1.0")),
        ("ENFORCE_DAILY_LIMITS = True",
         check_value_in_file("src/config/usage_limits.py", "ENFORCE_DAILY_LIMITS = True")),

        # User creation checks
        ("src/db/users.py exists",
         check_file_exists("src/db/users.py")),
        ("create_enhanced_user defaults to 5.0 credits",
         check_value_in_file("src/db/users.py", "credits: float = 5.0")),
        ("Docstring mentions $5 credits",
         check_value_in_file("src/db/users.py", "$5 credits")),

        # Test file checks
        ("Integration test file exists",
         check_file_exists("tests/integration/test_trial_credits_with_daily_limits.py")),
        ("Daily usage limiter tests exist",
         check_file_exists("tests/services/test_daily_usage_limiter.py")),
        ("User credit update tests exist",
         check_file_exists("tests/db/test_user_credit_updates.py")),
        ("Tests assert 5.0 credits",
         check_value_in_file("tests/db/test_user_credit_updates.py", "assert insert_call[\"credits\"] == 5.0")),

        # Documentation checks
        ("CREDIT_FRAUD_MITIGATION.md exists",
         check_file_exists("CREDIT_FRAUD_MITIGATION.md")),
        ("TRIAL_CREDIT_UPDATE_SUMMARY.md exists",
         check_file_exists("TRIAL_CREDIT_UPDATE_SUMMARY.md")),
        ("RCCG_USERS_REPORT.md exists",
         check_file_exists("RCCG_USERS_REPORT.md")),
        ("TESTING_SUMMARY.md exists",
         check_file_exists("TESTING_SUMMARY.md")),
        ("TEST_TRIAL_CREDITS_GUIDE.md exists",
         check_file_exists("tests/integration/TEST_TRIAL_CREDITS_GUIDE.md")),
    ]

    for description, result in checks:
        if result:
            print(f"✓ {description}")
            passed += 1
        else:
            print(f"✗ {description}")
            failed += 1

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print()

    if failed == 0:
        print("✓ All checks passed!")
        print()
        print("Configuration Summary:")
        print("  • Trial credits: $5.00")
        print("  • Trial duration: 3 days")
        print("  • Daily limit: $1.00/day")
        print("  • Enforcement: ENABLED")
        print()
        print("User Journey:")
        print("  • Day 1-3: Use $1/day during trial ($3 total)")
        print("  • Day 4-5: Use remaining $2 post-trial ($1/day)")
        print("  • Total: 5 days of usage from $5 credits")
        print()
        return 0
    else:
        print("✗ Some checks failed. Please review the configuration.")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
