#!/usr/bin/env bash
set -Eeuo pipefail

echo '>>> Ensuring execution API Python dependencies...'
apt-get update -qq
apt-get install -y -qq python3-pip 2>&1 | tail -1
python3 -m pip install --break-system-packages -r /app/mt5docker/requirements.txt --quiet 2>&1

mkdir -p /app/runtime/execution
cd /app
echo '>>> Starting Demo-gated execution API...'
exec python3 -u -m service.entrypoints.execution_server
