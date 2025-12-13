#!/bin/bash
set -euo pipefail

# Script to check Sentry and Railway for backend errors in the last 24 hours

echo "=================================================="
echo "Checking Backend Errors (Last 24 Hours)"
echo "=================================================="
echo ""

# Get timestamp for 24 hours ago
TIMESTAMP_24H_AGO=$(date -u -d '24 hours ago' --iso-8601=seconds)
echo "Checking errors since: $TIMESTAMP_24H_AGO"
echo ""

# ======================
# SENTRY ERROR CHECK
# ======================
echo "=== Checking Sentry for Errors ==="
echo ""

if [ -z "${SENTRY_ACCESS_TOKEN:-}" ]; then
    echo "WARNING: SENTRY_ACCESS_TOKEN not set, skipping Sentry check"
else
    # Get Sentry organization and project from env or use defaults
    SENTRY_ORG="${SENTRY_ORG:-terragon-labs}"
    SENTRY_PROJECT="${SENTRY_PROJECT:-gatewayz-universal-inference-api}"
    SENTRY_HOST="${SENTRY_HOST:-https://sentry.io}"

    echo "Fetching issues from Sentry project: $SENTRY_ORG/$SENTRY_PROJECT"

    # Fetch unresolved issues from the last 24 hours
    SENTRY_RESPONSE=$(curl -s -X GET \
        "${SENTRY_HOST}/api/0/projects/${SENTRY_ORG}/${SENTRY_PROJECT}/issues/" \
        -H "Authorization: Bearer ${SENTRY_ACCESS_TOKEN}" \
        -H "Content-Type: application/json" \
        "${SENTRY_HOST}/api/0/projects/${SENTRY_ORG}/${SENTRY_PROJECT}/issues/?query=is:unresolved&statsPeriod=24h" \

    if [ "$SENTRY_RESPONSE" = "ERROR" ]; then
        echo "ERROR: Failed to fetch Sentry issues"
    else
        # Parse and display issues
        echo "$SENTRY_RESPONSE" | jq -r '
            if type == "array" then
                if length == 0 then
                    "✓ No unresolved issues found in Sentry (last 24h)"
                else
                    "Found \(length) unresolved issue(s):\n" +
                    (map(
                        "---\n" +
                        "Issue ID: \(.id)\n" +
                        "Title: \(.title)\n" +
                        "Level: \(.level)\n" +
                        "Count (24h): \(.count // "N/A")\n" +
                        "First Seen: \(.firstSeen)\n" +
                        "Last Seen: \(.lastSeen)\n" +
                        "Culprit: \(.culprit // "N/A")\n" +
                        "Link: \(.permalink)\n"
                    ) | join("\n"))
                end
            else
                "ERROR: Unexpected response format: \(.)"
            end
        ' 2>/dev/null || echo "ERROR: Failed to parse Sentry response"
    fi
fi

echo ""
echo "=== Checking Railway Logs ==="
echo ""

if [ -z "${RAILWAY_TOKEN:-}" ]; then
    echo "WARNING: RAILWAY_TOKEN not set, skipping Railway check"
else
    echo "Fetching Railway logs..."

    # Check if railway CLI is installed
    if ! command -v railway &> /dev/null; then
        echo "WARNING: Railway CLI not installed. Install with: npm i -g @railway/cli"
        echo "Attempting to fetch logs via Railway API..."

        # Get project info first
        PROJECT_INFO=$(curl -s -X POST \
            "https://backboard.railway.app/graphql/v2" \
            -H "Authorization: Bearer ${RAILWAY_TOKEN}" \
            -H "Content-Type: application/json" \
            -d '{"query": "query { me { projects { edges { node { id name services { edges { node { id name } } } } } } } }"}' 2>&1 || echo "ERROR")

        if [ "$PROJECT_INFO" = "ERROR" ]; then
            echo "ERROR: Failed to fetch Railway project info"
        else
            echo "Railway Projects:"
            echo "$PROJECT_INFO" | jq -r '.data.me.projects.edges[].node | "- \(.name) (ID: \(.id))"' 2>/dev/null || echo "ERROR parsing projects"

            # Extract first project and service ID
            PROJECT_ID=$(echo "$PROJECT_INFO" | jq -r '.data.me.projects.edges[0].node.id' 2>/dev/null)
            SERVICE_ID=$(echo "$PROJECT_INFO" | jq -r '.data.me.projects.edges[0].node.services.edges[0].node.id' 2>/dev/null)

            if [ -n "$PROJECT_ID" ] && [ -n "$SERVICE_ID" ] && [ "$PROJECT_ID" != "null" ] && [ "$SERVICE_ID" != "null" ]; then
                echo ""
                echo "Fetching logs for service: $SERVICE_ID"

                # Fetch deployment logs (Railway GraphQL API)
                LOGS_QUERY='{"query": "query deploymentLogs($serviceId: String!, $limit: Int) { deploymentLogs(serviceId: $serviceId, limit: $limit) { timestamp message severity } }", "variables": {"serviceId": "'$SERVICE_ID'", "limit": 100}}'

                LOGS_RESPONSE=$(curl -s -X POST \
                    "https://backboard.railway.app/graphql/v2" \
                    -H "Authorization: Bearer ${RAILWAY_TOKEN}" \
                    -H "Content-Type: application/json" \
                    -d "$LOGS_QUERY" 2>&1 || echo "ERROR")

                if [ "$LOGS_RESPONSE" = "ERROR" ]; then
                    echo "ERROR: Failed to fetch logs"
                else
                    echo "$LOGS_RESPONSE" | jq -r '
                        if .data.deploymentLogs then
                            .data.deploymentLogs | map(
                                select(.severity == "error" or (.message | test("error|exception|failed|traceback"; "i")))
                            ) |
                            if length == 0 then
                                "✓ No error logs found in Railway (last 100 entries)"
                            else
                                "Found \(length) error log entries:\n" +
                                (map(
                                    "---\n" +
                                    "Time: \(.timestamp)\n" +
                                    "Severity: \(.severity)\n" +
                                    "Message: \(.message)\n"
                                ) | join("\n"))
                            end
                        else
                            "ERROR: Could not fetch deployment logs: \(.errors // .)"
                        end
                    ' 2>/dev/null || echo "ERROR: Failed to parse logs"
                fi
            else
                echo "ERROR: Could not extract project or service ID"
            fi
        fi
    else
        echo "Using Railway CLI..."
        railway logs --limit 200 2>&1 | grep -iE "(error|exception|failed|traceback)" || echo "✓ No errors found in Railway logs"
    fi
fi

echo ""
echo "=================================================="
echo "Error Check Complete"
echo "=================================================="
