#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
DOCKERFILE="$ROOT/mt5docker/Dockerfile"
START="$ROOT/mt5docker/start_server.sh"
HEALTH="$ROOT/mt5docker/healthcheck.sh"
PANEL="$ROOT/mt5docker/desktop/tint2rc"

for package in tint2 pcmanfm; do
    grep -qw "$package" "$DOCKERFILE" || { echo "missing desktop package: $package" >&2; exit 1; }
done
x11vnc_command="$(sed -n '/^[[:space:]]*x11vnc /,/remember_child/ { s/[[:space:]]*&[[:space:]]*remember_child.*$//; s/\\$//; p; }' "$START" | tr '\n' ' ')"
grep -Eq 'x11vnc .* -noprimary' <<<"$x11vnc_command"
grep -Eq 'x11vnc .* -sendclipboard' <<<"$x11vnc_command"
grep -Eq 'x11vnc .* -acceptclipboard' <<<"$x11vnc_command"
grep -q 'pcmanfm --desktop --profile MT5' "$START"
grep -q 'tint2 -c /root/.config/tint2/tint2rc' "$START"
if grep -q 'autocutsel' "$START"; then
    echo 'autocutsel must not compete with x11vnc clipboard ownership' >&2
    exit 1
fi
if grep -Eq 'x11vnc .*-(nosel|noclipboard|nosetclipboard)' <<<"$x11vnc_command"; then
    echo 'x11vnc clipboard support must remain enabled' >&2
    exit 1
fi
grep -q 'panel_items = TSC' "$PANEL"
grep -q 'pgrep -x tint2' "$HEALTH"
grep -q 'pgrep -x pcmanfm' "$HEALTH"
echo 'desktop shell and clipboard contract test passed'
