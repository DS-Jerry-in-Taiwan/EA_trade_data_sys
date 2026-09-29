#!/bin/bash

echo '>>> Phase 2A: Ensuring pip and installing dependencies...'
apt-get update -qq && apt-get install -y -qq python3-pip 2>&1 | tail -1
python3 -m pip install --break-system-packages -r /app/mt5docker/requirements.txt --quiet 2>&1

echo '>>> Phase 2A: Starting services...'

mkdir -p /app/service/logs
cd /app
# The canonical supervisor owns the three service.entrypoints processes.
exec python3 -u -m service.runtime.supervisor
