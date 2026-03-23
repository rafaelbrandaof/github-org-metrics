#!/bin/bash
set -e

CRON_SCHEDULE="${CRON_SCHEDULE:-0 6 * * 1}"

printenv | grep -E '^(GITHUB_TOKEN|GITHUB_ORG|TARGET_REPOS|DATA_DIR|COLLECT_MONTHS|PATH)=' > /etc/environment

echo "${CRON_SCHEDULE} root . /etc/environment && cd /app && /usr/local/bin/uv run python collector.py >> /var/log/collector.log 2>&1" > /etc/cron.d/collector
chmod 0644 /etc/cron.d/collector
crontab /etc/cron.d/collector

touch /var/log/collector.log

echo "=== GitHub Metrics Dashboard ==="
echo "Cron schedule: ${CRON_SCHEDULE}"
echo "Organization:  ${GITHUB_ORG:-augentic-tech}"
echo "Data dir:      ${DATA_DIR:-/app/data}"
echo "================================="

cron

exec uv run python dashboard/app.py
