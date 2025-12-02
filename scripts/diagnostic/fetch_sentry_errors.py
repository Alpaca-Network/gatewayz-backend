#!/usr/bin/env python3
"""
Fetch and analyze model accessibility errors from Sentry.
"""

import os
import sys
import requests

SENTRY_TOKEN = os.environ.get("SENTRY_ACCESS_TOKEN")
SENTRY_ORG = "alpaca-network"
SENTRY_PROJECT = "gatewayz-backend"
BASE_URL = "https://sentry.io/api/0"

def fetch_issues(period="7d", limit=20):
    """Fetch recent issues from Sentry."""
    url = f"{BASE_URL}/projects/{SENTRY_ORG}/{SENTRY_PROJECT}/issues/"
    params = {
        "statsPeriod": period,
        "limit": limit
    }
    headers = {
        "Authorization": f"Bearer {SENTRY_TOKEN}"
    }

    response = requests.get(url, params=params, headers=headers)
    response.raise_for_status()
    return response.json()

def format_issue(issue):
    """Format a single issue for display."""
    title = issue.get('title', '')
    if len(title) > 150:
        title = title[:147] + "..."

    return {
        'id': issue.get('shortId'),
        'title': title,
        'count': issue.get('count', 0),
        'status': issue.get('status'),
        'substatus': issue.get('substatus', 'N/A'),
        'priority': issue.get('priority', 'N/A'),
        'first_seen': issue.get('firstSeen'),
        'last_seen': issue.get('lastSeen'),
        'permalink': issue.get('permalink'),
        'metadata': issue.get('metadata', {})
    }

def main():
    """Main function."""
    print("=" * 80)
    print("Sentry Model Accessibility Errors - Last 7 Days")
    print("=" * 80)
    print()

    try:
        issues = fetch_issues(period="7d", limit=20)
        print(f"Total issues found: {len(issues)}")
        print()

        # Filter for model-related issues
        model_issues = []
        for issue in issues:
            title = issue.get('title', '').lower()
            if any(keyword in title for keyword in ['model', 'unavailable', 'http', 'error', 'timeout']):
                model_issues.append(issue)

        print(f"Model-related issues: {len(model_issues)}")
        print("=" * 80)
        print()

        # Display each issue
        for idx, issue in enumerate(model_issues[:15], 1):
            formatted = format_issue(issue)

            print(f"{idx}. [{formatted['id']}] {formatted['title']}")
            print(f"   Count: {formatted['count']} occurrences")
            print(f"   Status: {formatted['status']} ({formatted['substatus']})")
            print(f"   Priority: {formatted['priority']}")
            print(f"   First: {formatted['first_seen']}")
            print(f"   Last:  {formatted['last_seen']}")
            print(f"   Link:  {formatted['permalink']}")
            print()

        # Categorize errors
        print("=" * 80)
        print("Error Categories")
        print("=" * 80)
        print()

        categories = {
            'rate_limit': [],
            'unavailable': [],
            'timeout': [],
            'auth': [],
            'not_found': [],
            'other': []
        }

        for issue in model_issues:
            title = issue.get('title', '').lower()
            if '429' in title or 'rate limit' in title:
                categories['rate_limit'].append(issue)
            elif 'unavailable' in title or '503' in title:
                categories['unavailable'].append(issue)
            elif 'timeout' in title or '504' in title:
                categories['timeout'].append(issue)
            elif '401' in title or '403' in title or 'auth' in title:
                categories['auth'].append(issue)
            elif '404' in title or 'not found' in title:
                categories['not_found'].append(issue)
            else:
                categories['other'].append(issue)

        for category, items in categories.items():
            if items:
                total_count = sum(int(item.get('count', 0)) for item in items)
                print(f"• {category.replace('_', ' ').title()}: {len(items)} issues, {total_count:,} total occurrences")

    except Exception as e:
        print(f"Error fetching Sentry data: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
