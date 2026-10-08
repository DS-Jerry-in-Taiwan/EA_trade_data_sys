"""Publication identity prevents reads from an independently configured cache."""

import pytest
from types import SimpleNamespace

from service.history.repository import HistoryNotReadyError, HistoryRepository, history_storage_id
from service.history.worker import HistoryService
from service.config import Settings
from service.infrastructure.status.component_status import read_status


def test_worker_publishes_configured_directory_identity(tmp_path):
    root = tmp_path / 'alternate-history'
    status = tmp_path / 'status.json'
    settings = Settings.from_mapping({'history_service': {
        'data_path': str(root), 'status_path': str(status),
    }}, environ={})
    worker = HistoryService(settings=settings, mt5_client=SimpleNamespace(shutdown=lambda: None))
    try:
        worker._publish_status('healthy')
        assert read_status(str(status), 30)['storage_id'] == history_storage_id(root)
    finally:
        worker.close()


@pytest.mark.parametrize('fresh,identity', [(False, 'matching'), (True, 'wrong'), (True, None)])
def test_repository_rejects_stale_or_conflicting_publisher(tmp_path, fresh, identity):
    (tmp_path / 'BTC_M5.csv').write_text('old cached data')
    repository = HistoryRepository(str(tmp_path), publication_status=lambda: {
        'fresh': fresh,
        'storage_id': history_storage_id(tmp_path) if identity == 'matching' else identity,
    })
    with pytest.raises(HistoryNotReadyError):
        repository.read('BTC', 'M5')


def test_repository_reads_non_default_published_directory(tmp_path):
    root = tmp_path / 'history'
    root.mkdir()
    (root / 'BTC_M5.csv').write_text('time,close\n1,2\n')
    (root / 'BTC_M5.csv.ready').write_text('broker-BTC')
    repository = HistoryRepository(str(root), publication_status=lambda: {
        'fresh': True, 'storage_id': history_storage_id(root),
    })
    frame, source = repository.read('BTC', 'M5')
    assert source == 'broker-BTC'
    assert frame.iloc[0]['close'] == 2
