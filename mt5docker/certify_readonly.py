"""GET-only deployment certification with credential-safe summaries.

Run inside the execution service so the existing API key stays in its
environment. No response data, account identifiers, or secrets are printed.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def probe(base, path, key, *, execution):
    request = urllib.request.Request(
        base.rstrip('/') + '/api/v1' + path,
        headers={'X-API-Key': key}, method='GET',
    )
    try:
        response = urllib.request.urlopen(request, timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, TimeoutError):
        return {'path': path, 'passed': False, 'reachable': False}
    with response:
        status = response.code
        if path == '/openapi.yaml':
            # Certify publication, without printing contract contents.
            body = response.read()
            return {'path': path, 'http': status, 'passed': status == 200 and b'openapi:' in body}
        try:
            payload = json.load(response)
        except (ValueError, UnicodeDecodeError):
            return {'path': path, 'http': status, 'passed': False, 'json': False}
    envelope_ok = not execution or (
        isinstance(payload, dict) and payload.get('schema_version') == 1
        and not isinstance(payload.get('schema_version'), bool)
        and ('data' in payload or 'error' in payload)
    )
    ready = None
    if path == '/health' and isinstance(payload, dict):
        health = payload.get('data', {}) if execution else payload
        ready = health.get('ready') if isinstance(health, dict) else None
    result = {'path': path, 'http': status, 'contract_valid': envelope_ok,
              'passed': status == 200 and envelope_ok and (path != '/health' or ready is True)}
    if ready is not None:
        result['ready'] = ready
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execution-url', default='http://execution-service:8091')
    parser.add_argument('--data-url', default='http://trade-data-service:8090')
    args = parser.parse_args()
    key = os.environ.get(os.environ.get('EXECUTION_API_KEY_ENV', 'READONLY_API_KEY'))
    if not key:
        print(json.dumps({'certified': False, 'reason': 'api_key_not_configured'}))
        return 1
    probes = []
    for execution, base, paths in (
        (False, args.data_url, ['/health', '/account', '/positions', '/history/orders', '/history/deals', '/openapi.yaml']),
        (True, args.execution_url, ['/health', '/account', '/orders', '/positions', '/deals',
                                   '/symbols/XAUUSDm', '/symbols/EURUSDm', '/symbols/GBPUSDm',
                                   '/symbols/BTC', '/openapi.yaml']),
    ):
        for path in paths:
            probes.append(dict(service='execution' if execution else 'data',
                               **probe(base, path, key, execution=execution)))
    certified = all(item['passed'] for item in probes)
    print(json.dumps({'certified': certified, 'mutation_attempted': False, 'probes': probes}, sort_keys=True))
    return 0 if certified else 1


if __name__ == '__main__':
    sys.exit(main())
