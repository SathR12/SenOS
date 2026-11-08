import socket
import sys

import pytest

from benchmarks.scenarios import web_demo as demo


def test_cli_uses_current_python_instead_of_assuming_sibling_executable(tmp_path):
    args = demo.controller_arguments('ai', tmp_path)
    assert args[:3] == [sys.executable, '-c', 'from senos.cli import app; app()']
    assert '--apply' in args and str(tmp_path/'registry.sqlite3') in args


def test_busy_port_selects_available_port_without_starting_workloads():
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        server = demo.bind_server(occupied.getsockname()[1])
        try:
            assert server.server_address[1] != occupied.getsockname()[1]
        finally:
            server.server_close()


def test_summary_keeps_baseline_after_chart_rolls_over_and_counts_failures():
    state = demo.DemoState('deterministic')
    state.add_sample(dict(phase='Baseline', ms=400, batches=1, timestamp=1, web=100, background=100, error=None))
    for i in range(200):
        state.add_sample(dict(phase='Prioritized', ms=300, batches=i+2, timestamp=i+2, web=200, background=100, error=None))
    state.add_sample(dict(phase='Prioritized', ms=None, batches=203, timestamp=203, web=200, background=100, error='TimeoutError'))
    data = state.snapshot()
    assert len(data['samples']) == 180
    assert data['summary']['Baseline']['mean_ms'] == 400
    assert data['summary']['Prioritized']['mean_ms'] == 300
    assert data['summary']['Prioritized']['errors'] == 1
    assert data['summary']['Prioritized']['successes'] == 200
    assert data['latency_reduction_percent'] == 25


def test_no_action_is_not_reported_as_a_success():
    state = demo.DemoState('ai')
    state.controller_finished(0, applied=False, restored=True)
    assert state.snapshot()['status'] == 'no_action'
    assert state.snapshot()['latency_reduction_percent'] is None


def test_controller_failure_is_visible_and_cannot_be_called_complete():
    state = demo.DemoState('ai')
    state.controller_finished(1, applied=True, restored=False)
    assert state.snapshot()['status'] == 'failed'


def test_direct_launch_resolves_artifacts_from_repository_not_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert demo.ROOT.joinpath('pyproject.toml').is_file()
    assert demo.STATE_ROOT == demo.ROOT/'var'/'web-demo'


def test_progress_read_retries_transient_file_replacement_gap(tmp_path, monkeypatch):
    path = tmp_path/'background.json'
    reads = iter([FileNotFoundError('rename gap'), '{"completed_batches": 42}'])
    def read(*args, **kwargs):
        value = next(reads)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(type(path), 'read_text', read)
    monkeypatch.setattr(demo.time, 'sleep', lambda seconds: None)
    assert demo.read_json(path)['completed_batches'] == 42


def test_progress_read_does_not_hide_permanently_missing_files(tmp_path):
    with pytest.raises(RuntimeError, match='Cannot read demo state'):
        demo.read_json(tmp_path/'missing.json', seconds=0)


def test_startup_failure_is_saved_and_cleanup_still_runs(monkeypatch, tmp_path):
    import threading
    cleaned = []
    class FailedServices:
        def __init__(self, *args):
            pass
        def start(self, *args):
            raise FileNotFoundError('workload executable unavailable')
        def cleanup(self):
            cleaned.append(True)
            return []
    monkeypatch.setattr(demo, 'Services', FailedServices)
    state = demo.DemoState('deterministic')
    done = threading.Event()
    demo.experiment(state, tmp_path, 0, 'deterministic', threading.Event(), done)
    assert cleaned and done.is_set()
    saved = __import__('json').loads((tmp_path/'summary.json').read_text())
    assert saved['status'] == 'failed' and 'FileNotFoundError' in saved['errors'][0]
    assert not saved['applied']


def test_http_dashboard_metrics_and_favicon_are_served_without_file_errors():
    import json
    import threading
    from urllib.request import urlopen
    server = demo.bind_server(0)
    server.demo = demo.DemoState('deterministic')
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}'
        assert b'id="results"' in urlopen(url).read()
        assert json.load(urlopen(url+'/metrics'))['status'] == 'starting'
        assert urlopen(url+'/favicon.ico').status == 204
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_stop_during_warmup_is_not_reported_as_running(monkeypatch, tmp_path):
    import threading
    cleaned = []
    class FakeServices:
        def __init__(self, *args): pass
        def start(self, mode, *args): return mode+'.service'
        def cleanup(self):
            cleaned.append(True)
            return []
    class FakeRegistry:
        def __init__(self, *args): pass
        def register(self, *args): pass
    monkeypatch.setattr(demo, 'Services', FakeServices)
    monkeypatch.setattr(demo, 'ready_state', lambda *args: {'port': 1234})
    monkeypatch.setattr(demo, 'Registry', FakeRegistry)
    stop, done = threading.Event(), threading.Event()
    stop.set()
    state = demo.DemoState('deterministic')
    demo.experiment(state, tmp_path, 0, 'deterministic', stop, done)
    assert cleaned and done.is_set()
    assert state.snapshot()['status'] == 'stopped'


def test_background_rate_uses_elapsed_counter_time_not_total_batches():
    state = demo.DemoState('deterministic')
    for phase, first, last, duration in [('Baseline', 0, 100, 10), ('Prioritized', 100, 400, 60)]:
        for batches, timestamp in [(first, 0), (last, duration)]:
            state.add_sample(dict(phase=phase, ms=100, batches=batches, timestamp=timestamp,
                background_timestamp=timestamp, web=100, background=100, error=None))
    summary = state.snapshot()['summary']
    assert summary['Baseline']['background_batches_per_second'] == 10
    assert summary['Prioritized']['background_batches_per_second'] == 5
    assert summary['Prioritized']['background_batches'] == 300
    assert summary['Prioritized']['measurement_seconds'] == 60
