#!/bin/bash

echo '>>> Phase 2A: Ensuring pip and installing dependencies...'
apt-get update -qq && apt-get install -y -qq python3-pip 2>&1 | tail -1
python3 -m pip install --break-system-packages -r /app/mt5docker/requirements.txt --quiet 2>&1

echo '>>> Phase 2A: Starting services...'

mkdir -p /app/service/logs

nohup python3 /app/service/tick_service.py > /app/service/logs/tick_service.log 2>&1 &
echo "  ✓ tick_service.py (PID: $!)"

nohup python3 /app/service/history_service.py > /app/service/logs/history_service.log 2>&1 &
echo "  ✓ history_service.py (PID: $!)"

nohup python3 /app/service/api_gateway.py > /app/service/logs/api_gateway.log 2>&1 &
echo "  ✓ api_gateway.py (PID: $! on port 8090)"

echo '>>> Phase 2A: All services started.'
echo '>>> Logs: tail -f /app/service/logs/*.log'

tail -f /dev/null
