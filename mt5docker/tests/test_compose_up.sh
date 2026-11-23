#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
SCRIPT="$ROOT/mt5docker/compose_up.sh"
OVERRIDE="$(mktemp)"
rm -f "$OVERRIDE"
profile_env="$(mktemp)"
mkdir -p /tmp/test-data/Config
printf opaque > /tmp/test-data/Config/accounts.dat
printf '[Common]\nKeepPrivate=1\n' > /tmp/test-config.ini
chmod 600 "$profile_env"
PYTHONPATH="$ROOT" python3 "$ROOT/mt5docker/profile_selector.py" --config /tmp/test-config.ini --data-dir /tmp/test-data --output "$profile_env" >/dev/null
missing_output="$(MT5_RUNTIME_OVERRIDE="$OVERRIDE" MT5_PROFILE_ENV="$profile_env" "$SCRIPT" config 2>&1 || true)"
printf '%s\n' "$missing_output" | grep -Fq 'required runtime compose override is missing' || {
    echo 'FAIL: compose_up must explain missing runtime override' >&2
    exit 1
}

printf '# test fixture\n' > "$OVERRIDE"
fake_bin="$(mktemp -d)"
cat > "$fake_bin/docker" <<'FAKE_DOCKER'
#!/usr/bin/env bash
printf '%s\n' "$*" > "${COMPOSE_UP_CAPTURE:?}"
FAKE_DOCKER
chmod +x "$fake_bin/docker"
capture="$(mktemp)"
COMPOSE_UP_CAPTURE="$capture" PATH="$fake_bin:$PATH" MT5_RUNTIME_OVERRIDE="$OVERRIDE" MT5_PROFILE_ENV="$profile_env" "$SCRIPT" config
grep -Fq -- "-f $OVERRIDE" "$capture" || {
    echo 'FAIL: compose_up did not pass the runtime override to docker compose' >&2
    exit 1
}
rm -rf "$fake_bin" "$profile_env" "$capture" "$OVERRIDE"
echo 'compose_up runtime override contract test passed'
