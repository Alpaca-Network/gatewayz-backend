#!/bin/sh
# Alertmanager entrypoint script with environment variable substitution
# Fixes: SMTP credentials not being substituted from environment variables

set -e

# Check if SMTP credentials are provided
if [ -z "$SMTP_USERNAME" ] || [ -z "$SMTP_PASSWORD" ]; then
    echo "WARNING: SMTP_USERNAME or SMTP_PASSWORD not set. Email alerts will fail."
    echo "Please set these environment variables for email notifications to work."
fi

# Substitute environment variables in alertmanager config
envsubst < /etc/alertmanager/alertmanager.yml.template > /etc/alertmanager/alertmanager.yml

echo "✅ Alertmanager configuration processed with environment variables"
echo "   SMTP_USERNAME: ${SMTP_USERNAME:-not set}"
# Show whether password is configured (not the actual value)
if [ -n "$SMTP_PASSWORD" ]; then echo "   SMTP_PASSWORD: [configured]"; else echo "   SMTP_PASSWORD: not set"; fi

# Start Alertmanager with provided arguments
exec /bin/alertmanager "$@"
