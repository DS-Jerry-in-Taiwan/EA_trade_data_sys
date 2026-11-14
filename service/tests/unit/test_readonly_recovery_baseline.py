"""Synthetic recovery diagnostics only: no MT5, production key, or real HTTP."""
import copy
import importlib.util
import io
import json
from pathlib import Path
from unittest import mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    'readonly_recovery_probe', ROOT / 'mt5docker/probe_execution_readonly.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def fixture(path):
    session = {'fingerprint': {'id': 'synthetic-fingerprint'}, 'generation': 1,
               'epoch': 'synthetic-epoch', 'ready': True}
    if path == '/health':
        data = {'ready': True, 'mt5_connected': True, 'status': 'healthy',
                'account_mode': 'DEMO', 'account_policy': 'DEMO',
                'mutation_enabled': False, 'mutation_ready': False,
                'account_session': session, 'execution_authorization': {
                    'authorization_required': True, 'entry': {'enabled': False},
                    'exit': {'enabled': False}}}
    elif path == '/account':
        data = dict.fromkeys('account_id balance equity margin free_margin'.split(), 'synthetic')
        data.update(currency='USD', observed_at='2026-11-14T00:00:00Z')
    elif path in {'/orders', '/positions', '/deals'}:
        data = []
    else:
        symbol = path.split('/')[2]
        data = dict.fromkeys(probe.SPEC, '1')
        data.update(symbol=symbol, broker_symbol=symbol+'.synthetic', bid='100', ask='101',
                    quote_at='2026-11-13T23:59:59Z', observed_at='2026-11-14T00:00:00Z',
                    age_seconds='1', max_age_seconds=5, fresh=True,
                    account_session=dict(session, fingerprint='synthetic-fingerprint', account_mode='DEMO'))
        if path.endswith('/risk'):
            data.update(dict.fromkeys(probe.RISK, '1'))
            data.update(tick_value_currency='USD', account_currency='USD',
                        currency_relationship='order_calc_profit_account_currency',
                        loss_value_basis='max_mt5_loss_and_buy_sell_one_tick_loss_per_lot',
                        entry_deviation_points=20, close_deviation_points=20,
                        deviation_price='20', risk_ready=True)
    return 200, {'schema_version': 1, 'data': data}


def test_happy_diagnostic_is_redacted_and_never_certifies_freshness_or_mutation():
    paths = []
    def fetch(path):
        paths.append(path)
        return fixture(path)
    result = probe.run(fetch)
    assert result['request_count'] == 18
    assert result['read_only_contract_pass'] and result['session_stable']
    assert result['quote_risk_mapping_match'] and result['quote_risk_reported_fresh']
    assert result['current_utc_freshness_certified'] is False
    assert result['mutation_attempted'] is False and result['mutation_certified'] is False
    assert 'synthetic-fingerprint' not in json.dumps(result)
    assert 'synthetic-epoch' not in json.dumps(result)
    assert not any('/by-client/' in path for path in paths)


@pytest.mark.parametrize('field', ['execution_authorization', 'account_session'])
@pytest.mark.parametrize('value', [None, {}, [], ['bad'], 'bad'])
def test_malformed_health_fails_closed(field, value):
    def fetch(path):
        status, body = fixture(path)
        if path == '/health':
            body['data'][field] = value
        return status, body
    assert probe.run(fetch)['read_only_contract_pass'] is False


@pytest.mark.parametrize('gate', ['entry', 'exit'])
def test_enabled_gate_rejected(gate):
    def fetch(path):
        status, body = fixture(path)
        if path == '/health':
            body['data']['execution_authorization'][gate]['enabled'] = True
        return status, body
    assert probe.run(fetch)['read_only_contract_pass'] is False


@pytest.mark.parametrize('version', ['1', True, 2, None])
def test_strict_envelope_version(version):
    def fetch(path):
        status, body = fixture(path)
        body['schema_version'] = version
        return status, body
    assert probe.run(fetch)['read_only_contract_pass'] is False


@pytest.mark.parametrize('field,value', [('fingerprint', 'other'), ('generation', 2), ('epoch', 'other')])
def test_quote_session_mismatch(field, value):
    def fetch(path):
        status, body = fixture(path)
        if path.endswith('/quote'):
            body['data']['account_session'][field] = value
        return status, body
    assert probe.run(fetch)['read_only_contract_pass'] is False


def test_session_change_between_health_brackets():
    calls = 0
    def fetch(path):
        nonlocal calls
        status, body = fixture(path)
        if path == '/health':
            calls += 1
            if calls == 2:
                body['data']['account_session']['epoch'] = 'changed'
        return status, body
    result = probe.run(fetch)
    assert not result['session_stable'] and not result['read_only_contract_pass']


@pytest.mark.parametrize('age', ['-7200', '60'])
def test_future_and_stale_are_readable_but_not_reported_fresh(age):
    import datetime as dt
    def fetch(path):
        status, body = fixture(path)
        if path.endswith(('/quote', '/risk')):
            data = body['data']
            observed = dt.datetime.fromisoformat(data['observed_at'].replace('Z', '+00:00'))
            data.update(quote_at=(observed-dt.timedelta(seconds=float(age))).isoformat(),
                        age_seconds=age, fresh=False)
            if path.endswith('/risk'):
                data['risk_ready'] = False
        return status, body
    result = probe.run(fetch)
    assert result['read_only_contract_pass'] and not result['quote_risk_reported_fresh']


def test_old_consistent_payload_is_not_current_utc_certification():
    # Fixed historical timestamps in fixture are intentionally not compared to now.
    result = probe.run(fixture)
    assert result['quote_risk_reported_fresh']
    assert result['current_utc_freshness_certified'] is False


@pytest.mark.parametrize('field,value', [('loss_tick_value', 'NaN'),
                                      ('tick_value_currency', 'other'),
                                      ('entry_deviation_points', True),
                                      ('close_deviation_points', 1),
                                      ('risk_ready', False)])
def test_invalid_risk_facts(field, value):
    def fetch(path):
        status, body = fixture(path)
        if path.endswith('/risk'):
            body['data'][field] = value
        return status, body
    assert probe.run(fetch)['read_only_contract_pass'] is False


def test_quote_risk_broker_mismatch():
    def fetch(path):
        status, body = fixture(path)
        if path.endswith('/risk'):
            body['data']['broker_symbol'] = 'other'
        return status, body
    result = probe.run(fetch)
    assert not result['quote_risk_mapping_match'] and not result['read_only_contract_pass']


def test_unavailable_does_not_report_empty_exposure():
    result = probe.run(lambda path: (503, None))
    assert not result['read_only_contract_pass']
    assert all('count' not in row for row in result['checks'])


def test_key_file_permissions_and_symlink(tmp_path):
    key = tmp_path / 'fake-key'
    key.write_text('synthetic-only')
    key.chmod(0o600)
    assert probe.read_key(key) == 'synthetic-only'
    key.chmod(0o644)
    with pytest.raises(ValueError):
        probe.read_key(key)
    link = tmp_path / 'link'
    link.symlink_to(key)
    with pytest.raises(OSError):
        probe.read_key(link)


def test_cli_makes_only_get_requests_and_redacts_key(capsys, monkeypatch):
    calls = []
    class Response(io.BytesIO):
        status = 200
    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            path = request.full_url.split('/api/v1', 1)[1]
            return Response(json.dumps(fixture(path)[1]).encode())
    monkeypatch.setattr(probe, 'read_key', lambda path: 'synthetic-private-key')
    monkeypatch.setattr(probe.urllib.request, 'build_opener', lambda *handlers: Opener())
    monkeypatch.setattr(probe.sys, 'argv', ['probe', '--base-url', 'https://example.invalid/api/v1',
                                         '--key-file', 'synthetic-file'])
    assert probe.main() == 0
    assert len(calls) == 18 and all(r.get_method() == 'GET' for r in calls)
    assert all(r.get_header('X-api-key') == 'synthetic-private-key' for r in calls)
    assert 'synthetic-private-key' not in capsys.readouterr().out
    assert probe.NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'https://elsewhere.invalid') is None


@pytest.mark.parametrize('base', ['https://example.invalid/wrong',
                                'https://user:pass@example.invalid/api/v1',
                                'https://example.invalid/api/v1?x=1'])
def test_invalid_origin_rejected_before_key_read(base, capsys, monkeypatch):
    reader = mock.Mock(side_effect=AssertionError('must not read'))
    monkeypatch.setattr(probe, 'read_key', reader)
    monkeypatch.setattr(probe.sys, 'argv', ['probe', '--base-url', base, '--key-file', 'fake'])
    assert probe.main() == 2
    reader.assert_not_called()
    assert json.loads(capsys.readouterr().out)['probe_setup_ok'] is False


def test_example_loads_typed_settings_without_changing_default():
    from service.config import load_settings
    settings = load_settings(ROOT/'service/config/examples/ftmo-demo.yaml', environ={})
    assert settings.connection.mode == 'terminal'
    assert settings.symbol_aliases == {'XAUUSDm': 'XAUUSD', 'EURUSDm': 'EURUSD',
                                       'GBPUSDm': 'GBPUSD', 'BTC': 'BTCUSD'}
    assert settings.tick_service.symbols == ('XAUUSDm', 'EURUSDm', 'GBPUSDm', 'BTC')
    assert all(s.timeframes == ('M5', 'M15', 'H1', 'D1') for s in settings.history_service.symbols)


def test_baseline_manifest_contract_versions_and_safety():
    data = json.loads((ROOT/'releases/ftmo-readonly-baseline-2026.11.14.1.json').read_text())
    assert data['tag'] == 'ftmo-readonly-baseline-2026.11.14.1'
    for name, key in [('openapi.yaml', 'data_openapi_version'),
                      ('execution_openapi.yaml', 'execution_openapi_version')]:
        assert yaml.safe_load((ROOT/'service'/name).read_text())['info']['version'] == data[key]
    assert data['execution_schema_version'] == 1
    assert data['mutation_certified'] is False
    assert data['current_utc_freshness_certified'] is False
    assert data['deployment_reproducibility_certified'] is False
