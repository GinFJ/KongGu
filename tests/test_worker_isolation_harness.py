"""The isolation driver strips development environment and honors NDJSON envelopes."""
import io
from pathlib import Path
import queue
from types import SimpleNamespace

from tools.verify_worker_isolation import WorkerSession, isolated_environment


def test_isolation_does_not_inherit_python_or_model_cache_variables(tmp_path):
    environment = isolated_environment(tmp_path, dict(
        SystemRoot=r'C:\Windows', PATH=r'D:\developer\Python;D:\project\.venv',
        PYTHONPATH='private-code', PYTHONHOME='developer-python',
        VIRTUAL_ENV='developer-venv', KONGGU_BUNDLED_RESOURCE_ROOT='developer-models',
        PADDLE_HOME='old-model-cache', KONGGU_OCR_LAYOUT_CACHE='old-ocr-cache',
    ))
    assert environment['PATH'] == str(Path(r'C:\Windows')/'System32')
    for key in ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'KONGGU_BUNDLED_RESOURCE_ROOT',
                'PADDLE_HOME', 'KONGGU_OCR_LAYOUT_CACHE']:
        assert key not in environment
    assert environment['KONGGU_APP_DATA_ROOT'] == str(tmp_path/'appdata')
    assert environment['KONGGU_OCR_MODEL_DIR'] == str(tmp_path/'models')


def test_isolation_driver_uses_the_real_nested_ndjson_reply_payload():
    session = WorkerSession.__new__(WorkerSession)
    session.counter = 0
    session.timeout = 1
    session.process = SimpleNamespace(stdin=io.StringIO())
    session.messages = queue.Queue()
    session.messages.put(dict(type='event', event='progress'))
    session.messages.put(dict(id='1', type='response', ok=True, data=dict(ok=True, ready=True)))
    assert session.request('resources.repair') == dict(ok=True, ready=True)
    session.messages.put(dict(id='2', type='response', ok=False, error='expected rejection'))
    assert session.request('exports.excel')['error'] == 'expected rejection'
