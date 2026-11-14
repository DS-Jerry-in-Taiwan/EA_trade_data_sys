"""Offline CLI contracts: replace the external pytest process, never call MT5."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


RUNNER = Path(__file__).resolve().parents[1] / 'e2e' / 'run_e2e.sh'


def run_runner(tmp_path, failed_call=0, failure_code=1):
    runner = tmp_path / 'run_e2e.sh'
    shutil.copyfile(RUNNER, runner)
    binary = tmp_path / 'bin'
    binary.mkdir()
    fake = binary / 'python3'
    fake.write_text('''#!/usr/bin/env bash
count=0
if [[ -f "$RUNNER_CALLS" ]]; then read -r count < "$RUNNER_CALLS"; fi
count=$((count+1))
printf '%s\\n' "$count" > "$RUNNER_CALLS"
echo "synthetic pytest call $count"
if [[ "$count" == "$FAILED_CALL" ]]; then exit "$FAILURE_CODE"; fi
exit 0
''')
    fake.chmod(0o700)
    calls = tmp_path / 'calls'
    env = dict(os.environ, PATH=f'{binary}{os.pathsep}{os.environ["PATH"]}',
               RUNNER_CALLS=str(calls), FAILED_CALL=str(failed_call),
               FAILURE_CODE=str(failure_code), E2E_RESULTS_DIR=str(tmp_path / 'results'))
    result = subprocess.run(['bash', str(runner)], env=env, capture_output=True,
                            text=True, timeout=10)
    return result, int(calls.read_text())


@pytest.mark.parametrize('failed_call', [1, 2, 3, 4, 5, 6, 7])
def test_failure_cannot_be_hidden_by_later_success(tmp_path, failed_call):
    result, calls = run_runner(tmp_path, failed_call)
    assert result.returncode != 0, result.stdout
    assert calls == 7


def test_all_success_returns_zero(tmp_path):
    result, calls = run_runner(tmp_path)
    assert result.returncode == 0, result.stdout
    assert calls == 7


@pytest.mark.parametrize('code', [2, 3, 4, 5])
def test_pytest_errors_and_no_tests_fail_the_runner(tmp_path, code):
    result, calls = run_runner(tmp_path, failed_call=1, failure_code=code)
    assert result.returncode != 0, result.stdout
    assert calls == 7
