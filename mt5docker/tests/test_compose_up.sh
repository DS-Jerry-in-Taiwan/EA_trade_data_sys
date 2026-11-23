#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
SCRIPT="$ROOT/mt5docker/compose_up.sh"
OVERRIDE="$(mktemp)"
rm -f "$OVERRIDE"
profile_env="$(mktemp)"
printf 'MT5_CONFIG_FILE=/tmp/test-config.ini\nMT5_DATA_DIR=/tmp/test-data\n' > "$profile_env"
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
