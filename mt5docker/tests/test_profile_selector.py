import stat
import shlex
import subprocess
from pathlib import Path
import pytest
from mt5docker.profile_selector import ProfileSelectionError, select_profile, write_env

def test_select_profile_requires_native_account_store(tmp_path: Path) -> None:
    config = tmp_path / "demo.ini"
    data = tmp_path / "data"
    config.write_text("[Common]\nKeepPrivate=1\n", encoding="utf-8")
    data.mkdir()
    with pytest.raises(ProfileSelectionError, match="native account store"):
        select_profile(config, data)

def test_select_profile_writes_non_secret_selection(tmp_path: Path) -> None:
    config = tmp_path / "demo.ini"
    data = tmp_path / "data"
    (data / "Config").mkdir(parents=True)
    (data / "Config/accounts.dat").write_bytes(b"opaque")
    config.write_text("[Common]\nKeepPrivate=1\n", encoding="utf-8")
    values = select_profile(config, data)
    output = tmp_path / "active-profile.env"
    write_env(values, output)
    assert values["MT5_PROFILE_ID"] == "demo"
    assert values["MT5_CONFIG_FILE"] == str(config.resolve())
    assert values["MT5_DATA_DIR"] == str(data.resolve())
    assert len(values["MT5_PROFILE_FINGERPRINT"]) == 16
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert "opaque" not in output.read_text(encoding="utf-8")

def test_missing_native_session_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "demo.ini"
    data = tmp_path / "data"
    config.write_text("[Common]\nKeepPrivate=1\n", encoding="utf-8")
    data.mkdir()
    with pytest.raises(ProfileSelectionError, match="native account store"):
        select_profile(config, data)


def test_write_env_can_be_sourced_when_paths_contain_spaces(tmp_path: Path) -> None:
    config = tmp_path / "config with space.ini"
    data = tmp_path / "data with space"
    (data / "Config").mkdir(parents=True)
    (data / "Config/accounts.dat").write_bytes(b"opaque")
    config.write_text("[Common]\n", encoding="utf-8")
    values = select_profile(config, data)
    output = tmp_path / "active-profile.env"
    write_env(values, output)
    shell = (
        "set -a; . "
        + shlex.quote(str(output))
        + "; printf '%s\\n%s' \"$MT5_CONFIG_FILE\" \"$MT5_DATA_DIR\""
    )
    result = subprocess.run(["bash", "-c", shell], check=True, capture_output=True, text=True)
    assert result.stdout.splitlines() == [str(config.resolve()), str(data.resolve())]


def test_native_session_does_not_require_bootstrap_config(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "Config").mkdir(parents=True)
    (data / "Config/accounts.dat").write_bytes(b"opaque")
    values = select_profile(None, data)
    assert values["MT5_CONFIG_FILE"].endswith("native-session-placeholder.ini")


@pytest.mark.parametrize("relative_store", ["Config/accounts.dat", "config/accounts.dat"])
def test_native_session_accepts_lifecycle_account_store_paths(tmp_path: Path, relative_store: str) -> None:
    data = tmp_path / "data"
    store = data / relative_store
    store.parent.mkdir(parents=True)
    store.write_bytes(b"opaque")
    values = select_profile(None, data)
    assert values["MT5_DATA_DIR"] == str(data.resolve())


def test_native_session_rejects_ambiguous_account_store_paths(tmp_path: Path) -> None:
    data = tmp_path / "data"
    for relative_store in ("Config/accounts.dat", "config/accounts.dat"):
        store = data / relative_store
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_bytes(b"opaque")
    with pytest.raises(ProfileSelectionError, match="ambiguous"):
        select_profile(None, data)


def test_write_env_rejects_unknown_keys(tmp_path: Path) -> None:
    with pytest.raises(ProfileSelectionError, match="unexpected key"):
        write_env({"UNSAFE": "value"}, tmp_path / "active.env")
