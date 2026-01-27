#!/usr/bin/env python3
"""
Production Readiness Verification Script
Performs read-only checks to verify production environment is ready for pricing scheduler deployment.

GitHub Issue: #960
"""

import sys
import json
import requests
from datetime import datetime, timezone
from typing import Dict, List, Tuple
import os

# ANSI color codes for terminal output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

class ProductionVerifier:
    def __init__(self, production_url: str = "https://api.gatewayz.ai", admin_key: str = None):
        self.production_url = production_url
        self.admin_key = admin_key or os.getenv("PROD_ADMIN_KEY")
        self.results = []
        self.checks_passed = 0
        self.checks_failed = 0

    def log_check(self, step: str, check_name: str, passed: bool, details: str = ""):
        """Log a verification check result"""
        status = f"{Colors.GREEN}✅ PASS{Colors.END}" if passed else f"{Colors.RED}❌ FAIL{Colors.END}"
        print(f"\n{Colors.BOLD}[{step}] {check_name}{Colors.END}")
        print(f"Status: {status}")
        if details:
            print(f"Details: {details}")

        self.results.append({
            "step": step,
            "check": check_name,
            "passed": passed,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        if passed:
            self.checks_passed += 1
        else:
            self.checks_failed += 1

    def verify_health_endpoint(self) -> bool:
        """Step 3: Check production health endpoint"""
        print(f"\n{Colors.BLUE}{Colors.BOLD}{'='*80}{Colors.END}")
        print(f"{Colors.BLUE}{Colors.BOLD}STEP 3: Verify Production Health{Colors.END}")
        print(f"{Colors.BLUE}{Colors.BOLD}{'='*80}{Colors.END}")

        try:
            response = requests.get(f"{self.production_url}/health", timeout=10)
            passed = response.status_code == 200

            if passed:
                data = response.json()
                details = f"Status: {data.get('status')}, Database: {data.get('database')}"
                self.log_check("3", "Production health endpoint", True, details)
                return True
            else:
                self.log_check("3", "Production health endpoint", False, f"Status code: {response.status_code}")
                return False

        except Exception as e:
            self.log_check("3", "Production health endpoint", False, f"Error: {str(e)}")
            return False

    def verify_admin_endpoints(self) -> bool:
        """Step 4: Verify admin endpoints are available"""
        print(f"\n{Colors.BLUE}{Colors.BOLD}{'='*80}{Colors.END}")
        print(f"{Colors.BLUE}{Colors.BOLD}STEP 4: Verify Admin Endpoints{Colors.END}")
        print(f"{Colors.BLUE}{Colors.BOLD}{'='*80}{Colors.END}")

        if not self.admin_key:
            self.log_check("4", "Admin API key available", False, "No admin key provided")
            return False

        self.log_check("4", "Admin API key available", True, "Admin key found")

        # Check scheduler status endpoint
        try:
            headers = {"Authorization": f"Bearer {self.admin_key}"}
            response = requests.get(
                f"{self.production_url}/admin/pricing/scheduler/status",
                headers=headers,
                timeout=10
            )

            # 404 is acceptable if endpoint not deployed yet
            if response.status_code in [200, 404]:
                passed = True
                details = f"Status code: {response.status_code}"
                if response.status_code == 200:
                    details += f", Response: {response.json()}"
                else:
                    details += " (Not deployed yet - expected)"
            else:
                passed = False
                details = f"Status code: {response.status_code}, Response: {response.text[:200]}"

            self.log_check("4", "Admin scheduler status endpoint", passed, details)
            return passed

        except Exception as e:
            self.log_check("4", "Admin scheduler status endpoint", False, f"Error: {str(e)}")
            return False

    def verify_metrics_endpoint(self) -> bool:
        """Step 5: Check metrics endpoint"""
        print(f"\n{Colors.BLUE}{Colors.BOLD}{'='*80}{Colors.END}")
        print(f"{Colors.BLUE}{Colors.BOLD}STEP 5: Verify Metrics Endpoint{Colors.END}")
        print(f"{Colors.BLUE}{Colors.BOLD}{'='*80}{Colors.END}")

        try:
            response = requests.get(f"{self.production_url}/metrics", timeout=10)
            passed = response.status_code == 200

            if passed:
                metrics_text = response.text
                pricing_metrics = [line for line in metrics_text.split('\n') if 'pricing_' in line]
                details = f"Metrics endpoint accessible, found {len(pricing_metrics)} pricing metrics"
                if pricing_metrics:
                    details += f"\nSample: {pricing_metrics[0][:100]}"
                self.log_check("5", "Metrics endpoint accessible", True, details)
            else:
                self.log_check("5", "Metrics endpoint accessible", False, f"Status code: {response.status_code}")

            return passed

        except Exception as e:
            self.log_check("5", "Metrics endpoint accessible", False, f"Error: {str(e)}")
            return False

    def verify_database_schema(self) -> bool:
        """Step 2: Verify database migration (requires database access)"""
        print(f"\n{Colors.BLUE}{Colors.BOLD}{'='*80}{Colors.END}")
        print(f"{Colors.BLUE}{Colors.BOLD}STEP 2: Verify Database Schema{Colors.END}")
        print(f"{Colors.BLUE}{Colors.BOLD}{'='*80}{Colors.END}")

        # This requires direct database access - document manual check needed
        print(f"{Colors.YELLOW}⚠️  Manual verification required:{Colors.END}")
        print("   Execute the following SQL query in production database:")
        print()
        print("   SELECT tablename, schemaname")
        print("   FROM pg_tables")
        print("   WHERE schemaname = 'public'")
        print("   AND tablename IN ('model_pricing_history', 'pricing_sync_log')")
        print("   ORDER BY tablename;")
        print()
        print("   Expected tables:")
        print("   - model_pricing_history")
        print("   - pricing_sync_log")

        self.log_check("2", "Database schema verification", None, "Manual verification required - see output above")
        return None

    def verify_configuration(self) -> bool:
        """Step 8: Validate configuration differences"""
        print(f"\n{Colors.BLUE}{Colors.BOLD}{'='*80}{Colors.END}")
        print(f"{Colors.BLUE}{Colors.BOLD}STEP 8: Validate Configuration{Colors.END}")
        print(f"{Colors.BLUE}{Colors.BOLD}{'='*80}{Colors.END}")

        expected_config = {
            "PRICING_SYNC_INTERVAL_HOURS": "6",
            "PRICING_SYNC_PROVIDERS": "openrouter,featherless,nearai,alibaba-cloud",
            "PRICING_SYNC_ENABLED": "true (or false, ready to enable)"
        }

        print(f"{Colors.YELLOW}⚠️  Manual verification required:{Colors.END}")
        print("   Check production environment variables:")
        print()
        for key, expected in expected_config.items():
            print(f"   {key}={expected}")
        print()
        print("   Verify:")
        print("   - Interval is 6 hours (NOT 3)")
        print("   - All 4 providers configured")
        print("   - Scheduler ready to enable")

        self.log_check("8", "Configuration validation", None, "Manual verification required - see output above")
        return None

    def generate_report(self) -> Dict:
        """Generate comprehensive verification report"""
        print(f"\n{Colors.BLUE}{Colors.BOLD}{'='*80}{Colors.END}")
        print(f"{Colors.BLUE}{Colors.BOLD}VERIFICATION SUMMARY{Colors.END}")
        print(f"{Colors.BLUE}{Colors.BOLD}{'='*80}{Colors.END}")

        total_checks = self.checks_passed + self.checks_failed
        manual_checks = sum(1 for r in self.results if r['passed'] is None)

        print(f"\nAutomated Checks: {total_checks}")
        print(f"{Colors.GREEN}✅ Passed: {self.checks_passed}{Colors.END}")
        print(f"{Colors.RED}❌ Failed: {self.checks_failed}{Colors.END}")
        print(f"{Colors.YELLOW}⚠️  Manual Verification Needed: {manual_checks}{Colors.END}")

        report = {
            "verification_timestamp": datetime.now(timezone.utc).isoformat(),
            "production_url": self.production_url,
            "summary": {
                "total_automated_checks": total_checks,
                "passed": self.checks_passed,
                "failed": self.checks_failed,
                "manual_checks": manual_checks
            },
            "checks": self.results,
            "ready_for_deployment": self.checks_failed == 0
        }

        if self.checks_failed == 0:
            print(f"\n{Colors.GREEN}{Colors.BOLD}✅ All automated checks PASSED!{Colors.END}")
        else:
            print(f"\n{Colors.RED}{Colors.BOLD}❌ Some checks FAILED - review before deployment{Colors.END}")

        print(f"\n{Colors.YELLOW}Manual Verification Steps:{Colors.END}")
        print("1. Verify database schema (Step 2)")
        print("2. Validate environment variables (Step 8)")
        print("3. Complete deployment checklist in issue #960")

        return report

    def run_verification(self) -> Dict:
        """Run all verification checks"""
        print(f"{Colors.BOLD}{'='*80}{Colors.END}")
        print(f"{Colors.BOLD}Production Readiness Verification - Issue #960{Colors.END}")
        print(f"{Colors.BOLD}{'='*80}{Colors.END}")
        print(f"Production URL: {self.production_url}")
        print(f"Admin Key: {'✅ Provided' if self.admin_key else '❌ Missing'}")
        print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")

        # Run verification steps
        self.verify_health_endpoint()
        self.verify_admin_endpoints()
        self.verify_metrics_endpoint()
        self.verify_database_schema()  # Manual check
        self.verify_configuration()  # Manual check

        # Generate and save report
        report = self.generate_report()

        # Save report to file
        report_file = f"production_verification_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)

        print(f"\n{Colors.GREEN}📄 Report saved to: {report_file}{Colors.END}")

        return report


def main():
    """Main execution function"""
    import argparse

    parser = argparse.ArgumentParser(
        description='Verify production readiness for pricing scheduler deployment (Issue #960)'
    )
    parser.add_argument(
        '--admin-key',
        help='Production admin API key (or set PROD_ADMIN_KEY env var)',
        default=None
    )
    parser.add_argument(
        '--production-url',
        help='Production API URL',
        default='https://api.gatewayz.ai'
    )

    args = parser.parse_args()

    # Check for admin key
    admin_key = args.admin_key or os.getenv('PROD_ADMIN_KEY')
    if not admin_key:
        print(f"{Colors.YELLOW}⚠️  Warning: No admin key provided. Some checks will be limited.{Colors.END}")
        print(f"   Set PROD_ADMIN_KEY environment variable or use --admin-key flag")

    # Run verification
    verifier = ProductionVerifier(
        production_url=args.production_url,
        admin_key=admin_key
    )

    report = verifier.run_verification()

    # Exit with appropriate code
    if report['summary']['failed'] > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
