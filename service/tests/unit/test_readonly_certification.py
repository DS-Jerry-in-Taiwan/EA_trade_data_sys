import io
import json
from unittest.mock import patch

from mt5docker.certify_readonly import probe


class Response(io.BytesIO):
    code = 200


def test_certification_only_sends_get_and_never_reports_payload():
    payload = {'schema_version': 1, 'data': {'private_field': 'synthetic-private'}}
    with patch('urllib.request.urlopen', return_value=Response(json.dumps(payload).encode())) as opening:
        result = probe('http://synthetic', '/account', 'synthetic-key', execution=True)
    request = opening.call_args.args[0]
    assert request.get_method() == 'GET'
    assert request.data is None
    assert result['passed'] is True
    assert 'synthetic-private' not in json.dumps(result)
    assert 'synthetic-key' not in json.dumps(result)


def test_certification_rejects_unready_health():
    payload = {'schema_version': 1, 'data': {'ready': False}}
    with patch('urllib.request.urlopen', return_value=Response(json.dumps(payload).encode())):
        result = probe('http://synthetic', '/health', 'synthetic-key', execution=True)
    assert result['passed'] is False
