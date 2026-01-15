#!/usr/bin/env python3
"""
Monitor Model Pricing Warnings

This script continuously monitors Railway deployment logs for model pricing warnings
and sends alerts when new models are detected using default pricing.

Usage:
    python scripts/utilities/monitor_model_pricing.py [--interval SECONDS] [--output FILE]

Options:
    --interval SECONDS  Check interval in seconds (default: 300 = 5 minutes)
    --output FILE       Output file for warnings log (default: logs/model_pricing_warnings.log)
    --alert-webhook URL Webhook URL for alerts (optional)
"""

import os
import sys
import argparse
import time
import json
import logging
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import the extraction function from our utility
from scripts.utilities.extract_model_pricing_warnings import (
    extract_warnings_from_logs,
)


# Ensure logs directory exists before configuring file handler
Path('logs').mkdir(parents=True, exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/model_pricing_monitor.log')
    ]
)
logger = logging.getLogger('model_pricing_monitor')


class ModelPricingMonitor:
    """Monitor for model pricing warnings in Railway deployments."""

    def __init__(self, check_interval=300, output_file=None, alert_webhook=None):
        """
        Initialize the monitor.

        Args:
            check_interval (int): Seconds between checks
            output_file (str): Path to output log file
            alert_webhook (str): Optional webhook URL for alerts
        """
        self.check_interval = check_interval
        self.output_file = output_file or 'logs/model_pricing_warnings.log'
        self.alert_webhook = alert_webhook
        self.known_warnings = set()  # Track known model warnings
        self.last_check = None

        # Ensure output directory exists
        Path(self.output_file).parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"Model Pricing Monitor initialized")
        logger.info(f"Check interval: {check_interval}s")
        logger.info(f"Output file: {self.output_file}")

    def load_known_warnings(self):
        """Load previously seen warnings from the output file."""
        if os.path.exists(self.output_file):
            try:
                with open(self.output_file, 'r') as f:
                    for line in f:
                        if line.strip():
                            data = json.loads(line)
                            key = (data['provider'], data['original_model'])
                            self.known_warnings.add(key)
                logger.info(f"Loaded {len(self.known_warnings)} known warnings")
            except Exception as e:
                logger.error(f"Error loading known warnings: {e}")

    def fetch_latest_logs(self):
        """
        Fetch latest deployment logs from Railway.

        Returns:
            str: Raw log text, or None if fetch fails

        Note: This is a placeholder. In production, this would use the Railway API
        or Railway MCP server to fetch actual logs.
        """
        # TODO: Implement Railway API or MCP integration
        # For now, return None to indicate no new logs
        logger.debug("Log fetching not yet implemented - would use Railway API here")
        return None

    def check_for_new_warnings(self, log_text):
        """
        Check log text for new model pricing warnings.

        Args:
            log_text (str): Raw log text to analyze

        Returns:
            list: List of new warning dictionaries
        """
        if not log_text:
            return []

        warnings = extract_warnings_from_logs(log_text)
        new_warnings = []

        for provider, models in warnings.items():
            for model_info in models:
                key = (provider, model_info['original'])
                if key not in self.known_warnings:
                    new_warnings.append({
                        'timestamp': datetime.now().isoformat(),
                        'provider': provider,
                        'original_model': model_info['original'],
                        'normalized_model': model_info['normalized'],
                        'pricing_status': model_info['pricing']
                    })
                    self.known_warnings.add(key)

        return new_warnings

    def log_warning(self, warning):
        """
        Log a new warning to the output file.

        Args:
            warning (dict): Warning information
        """
        try:
            with open(self.output_file, 'a') as f:
                f.write(json.dumps(warning) + '\n')
            logger.info(f"New model warning logged: {warning['provider']}/{warning['original_model']}")
        except Exception as e:
            logger.error(f"Error logging warning: {e}")

    def send_alert(self, warnings):
        """
        Send alert for new warnings via webhook.

        Args:
            warnings (list): List of new warnings
        """
        if not self.alert_webhook or not warnings:
            return

        try:
            import requests

            alert_data = {
                'timestamp': datetime.now().isoformat(),
                'alert_type': 'model_pricing_warning',
                'count': len(warnings),
                'warnings': warnings
            }

            response = requests.post(
                self.alert_webhook,
                json=alert_data,
                timeout=10
            )

            if response.status_code == 200:
                logger.info(f"Alert sent successfully for {len(warnings)} new warnings")
            else:
                logger.warning(f"Alert webhook returned status {response.status_code}")

        except Exception as e:
            logger.error(f"Error sending alert: {e}")

    def run_check(self):
        """Run a single monitoring check."""
        logger.info("Running pricing warning check...")

        # Fetch latest logs
        log_text = self.fetch_latest_logs()

        if log_text:
            # Check for new warnings
            new_warnings = self.check_for_new_warnings(log_text)

            if new_warnings:
                logger.warning(f"Found {len(new_warnings)} new model pricing warnings!")

                # Log each warning
                for warning in new_warnings:
                    self.log_warning(warning)

                # Send alert if webhook configured
                self.send_alert(new_warnings)

                # Print summary
                print(f"\n⚠️  NEW MODEL PRICING WARNINGS DETECTED: {len(new_warnings)}")
                print("=" * 70)
                for w in new_warnings:
                    print(f"  {w['provider']}/{w['original_model']} → {w['normalized_model']}")
                print("=" * 70)
            else:
                logger.info("No new warnings detected")
        else:
            logger.debug("No new logs to check")

        self.last_check = datetime.now()

    def run(self):
        """Run the monitoring loop continuously."""
        logger.info("Starting continuous monitoring...")

        # Load existing warnings
        self.load_known_warnings()

        try:
            while True:
                self.run_check()

                # Sleep until next check
                logger.info(f"Next check in {self.check_interval} seconds...")
                time.sleep(self.check_interval)

        except KeyboardInterrupt:
            logger.info("Monitor stopped by user")
        except Exception as e:
            logger.error(f"Monitor error: {e}", exc_info=True)
            raise


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Monitor Railway deployment logs for model pricing warnings'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=300,
        help='Check interval in seconds (default: 300)'
    )
    parser.add_argument(
        '--output',
        type=str,
        default='logs/model_pricing_warnings.log',
        help='Output file for warnings log'
    )
    parser.add_argument(
        '--alert-webhook',
        type=str,
        help='Webhook URL for alerts (optional)'
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run once and exit (no continuous monitoring)'
    )

    args = parser.parse_args()

    # Create monitor instance
    monitor = ModelPricingMonitor(
        check_interval=args.interval,
        output_file=args.output,
        alert_webhook=args.alert_webhook
    )

    # Run once or continuously
    if args.once:
        logger.info("Running single check...")
        monitor.load_known_warnings()
        monitor.run_check()
        logger.info("Check complete")
    else:
        monitor.run()

    return 0


if __name__ == '__main__':
    sys.exit(main())
