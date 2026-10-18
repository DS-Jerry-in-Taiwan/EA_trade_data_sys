#!/usr/bin/env bash
# Read-only, bounded observation. Never opens native stores or marker contents.
set -Eeuo pipefail
if [ "$#" -lt 2 ] || [ "$#" -gt 4 ]; then
    echo 'Usage: observe_account_recovery.sh PORTABLE_ROOT BOOTSTRAP_MARKER [SAMPLES=1] [INTERVAL_SECONDS=1]' >&2
    exit 2
fi
portable_root="$1"; marker="$2"; samples="${3:-1}"; interval="${4:-1}"
if ! [[ "$samples" =~ ^[1-9][0-9]{0,3}$ && "$interval" =~ ^[1-9][0-9]?$ ]] ||
    (( samples > 3600 || interval > 60 || (samples - 1) * interval > 3600 )); then
    echo 'Samples must be 1..3600, interval 1..60 seconds, total waiting at most 3600 seconds.' >&2
    exit 2
fi
observe() {
    local label="$1" path="$2" metadata
    # One stat captures the tuple, including zero-size stores; tolerate removal
    # between observations. Fixed labels avoid printing account selectors/paths.
    if metadata="$(stat -L -c 'size=%s inode=%i mtime=%Y' -- "$path" 2>/dev/null)"; then
        printf 'sample=%s target=%s exists=true %s\n' "$sample" "$label" "$metadata"
    else
        printf 'sample=%s target=%s exists=false size=unknown inode=unknown mtime=unknown\n' "$sample" "$label"
    fi
}
echo 'observation_scope=metadata_only recovery_certified=false session_certified=false restart_certified=false'
for ((sample = 1; sample <= samples; sample++)); do
    printf 'sample=%s observed_at=%s\n' "$sample" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    observe Config/accounts.dat "$portable_root/Config/accounts.dat"
    observe config/accounts.dat "$portable_root/config/accounts.dat"
    observe bootstrap_marker "$marker"
    if (( sample < samples )); then sleep "$interval"; fi
done
