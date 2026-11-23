#!/usr/bin/env python3
"""Select one MT5 profile and its native persistent data directory."""
from __future__ import annotations
import argparse, hashlib, os, shlex, stat, tempfile
from pathlib import Path

class ProfileSelectionError(ValueError):
    pass

def _regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        mode = resolved.stat().st_mode
    except FileNotFoundError as exc:
        raise ProfileSelectionError(f"{label} does not exist") from exc
    if not stat.S_ISREG(mode):
        raise ProfileSelectionError(f"{label} must be a regular file")
    return resolved

def _data_directory(path: Path) -> tuple[Path, Path]:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise ProfileSelectionError("MT5 data directory does not exist")
    account_stores = [resolved / "Config" / "accounts.dat", resolved / "config" / "accounts.dat"]
    existing_stores = [store for store in account_stores if store.exists()]
    if len(existing_stores) > 1:
        raise ProfileSelectionError("native account store is ambiguous; keep only Config/accounts.dat or config/accounts.dat")
    if existing_stores:
        account_store = existing_stores[0]
        if not account_store.is_file():
            raise ProfileSelectionError("native account store is not a regular file")
        return resolved, account_store
    raise ProfileSelectionError("native account store is missing; log in through VNC before selecting this profile")

def select_profile(config_file: Path | None, data_dir: Path) -> dict[str, str]:
    config = _regular_file(config_file, "MT5 config") if config_file is not None else Path(__file__).with_name("native-session-placeholder.ini").resolve()
    data, account_store = _data_directory(data_dir)
    profile_id = config.stem if config_file is not None else data.name
    store_marker = "missing"
    if account_store is not None:
        metadata = account_store.stat()
        store_marker = f"{metadata.st_size}:{metadata.st_mtime_ns}"
    fingerprint = hashlib.sha256(f"{profile_id}|{config}|{data}|{store_marker}".encode()).hexdigest()[:16]
    return {"MT5_PROFILE_ID": profile_id, "MT5_CONFIG_FILE": str(config),
            "MT5_DATA_DIR": str(data), "MT5_PROFILE_FINGERPRINT": fingerprint}

def _fingerprint(values: dict[str, str], account_store: Path) -> str:
    metadata = account_store.stat()
    raw = f"{values['MT5_PROFILE_ID']}|{values['MT5_CONFIG_FILE']}|{values['MT5_DATA_DIR']}|{metadata.st_size}:{metadata.st_mtime_ns}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

def read_env(path: Path) -> dict[str, str]:
    resolved = path.expanduser().resolve()
    metadata = resolved.stat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ProfileSelectionError("active profile must be a regular owner-only file")
    if metadata.st_uid != os.getuid():
        raise ProfileSelectionError("active profile owner is invalid")
    values: dict[str, str] = {}
    for line in resolved.read_text(encoding="utf-8").splitlines():
        try:
            tokens = shlex.split(line, comments=False, posix=True)
        except ValueError as exc:
            raise ProfileSelectionError("active profile has invalid quoting") from exc
        if len(tokens) != 1 or "=" not in tokens[0]:
            raise ProfileSelectionError("active profile has an invalid assignment")
        key, value = tokens[0].split("=", 1)
        values[key] = value
    allowed = {"MT5_PROFILE_ID", "MT5_CONFIG_FILE", "MT5_DATA_DIR", "MT5_PROFILE_FINGERPRINT"}
    if set(values) != allowed:
        raise ProfileSelectionError("active profile contains an unexpected key")
    config = _regular_file(Path(values["MT5_CONFIG_FILE"]), "MT5 config")
    data, store = _data_directory(Path(values["MT5_DATA_DIR"]))
    if values["MT5_CONFIG_FILE"] != str(config) or values["MT5_DATA_DIR"] != str(data):
        raise ProfileSelectionError("active profile paths must be canonical")
    if values["MT5_PROFILE_FINGERPRINT"] != _fingerprint(values, store):
        raise ProfileSelectionError("active profile fingerprint is stale")
    return values

def write_env(values: dict[str, str], output: Path) -> None:
    allowed = {"MT5_PROFILE_ID", "MT5_CONFIG_FILE", "MT5_DATA_DIR", "MT5_PROFILE_FINGERPRINT"}
    if set(values) != allowed:
        raise ProfileSelectionError("active profile contains an unexpected key")
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ProfileSelectionError("active profile contains a newline")
    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".active-profile.", dir=output.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for key, value in values.items():
                # The output is sourced by compose_up.sh. Quote paths so
                # spaces and shell metacharacters cannot split assignments or
                # execute as shell syntax.
                stream.write(f"{key}={shlex.quote(value)}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    os.chmod(output, 0o600)

def main() -> int:
    parser = argparse.ArgumentParser(description="Select an MT5 profile without reading credentials")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--output", type=Path, default=Path.home() / ".config/ea-trading/active-profile.env")
    parser.add_argument("--read-env", type=Path)
    args = parser.parse_args()
    try:
        if args.read_env:
            values = read_env(args.read_env)
            for key in ("MT5_PROFILE_ID", "MT5_PROFILE_FINGERPRINT"):
                print(f"{key}\t{values[key]}")
            for key in ("MT5_CONFIG_FILE", "MT5_DATA_DIR"):
                print(f"{key}\t{values[key]}")
            return 0
        if not args.data_dir:
            parser.error("--data-dir is required when selecting a profile")
        values = select_profile(args.config, args.data_dir)
        write_env(values, args.output)
    except ProfileSelectionError as exc:
        parser.error(str(exc))
    print(f"active profile selected: {values['MT5_PROFILE_ID']}")
    print(f"profile fingerprint: {values['MT5_PROFILE_FINGERPRINT']}")
    print("native account store: present")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
