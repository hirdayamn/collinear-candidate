#!/usr/bin/env bash
set -e

# Start Redis if not already running
if ! redis-cli ping > /dev/null 2>&1; then
    redis-server --daemonize yes
    sleep 1
fi

if [ -f "/app/scripts/reconcile_data.py" ]; then
    python3 /app/scripts/reconcile_data.py || true
fi

# Run multi-criterion verifier
python3 /app/tests/verifier.py

# For backward compatibility, also write simple reward.txt
REWARD_JSON="/logs/verifier/reward.json"
REWARD_TXT="/logs/verifier/reward.txt"
mkdir -p /logs/verifier

if [ -f "$REWARD_JSON" ]; then
    OVERALL=$(python3 -c "import json; print(json.load(open('$REWARD_JSON'))['overall'])")
    echo "$OVERALL" > "$REWARD_TXT"
    if [ "$OVERALL" = "1.0" ]; then
        echo "Task Verification Passed."
    else
        echo "Task Verification Failed."
    fi
else
    echo "0" > "$REWARD_TXT"
    echo "Task Verification Failed (no reward.json)."
    exit 1
fi