import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_control():
    spec = importlib.util.spec_from_file_location('terminal_demo_control', ROOT/'scripts/demo/control.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launch_is_namespaced_bounded_and_uses_project_python():
    control = load_control()
    args = control.launch_args('web')
    assert 'senos-terminal-web.service' in args
    assert '--property=RuntimeMaxSec=1800' in args
    assert f'--setenv={control.MARKER}={control.ROOT}' in args
    assert sys.executable in args
    assert '--pty' in args and '--wait' in args
    assert '--setenv=DEMO_WORKERS=8' in control.launch_args('build')


def test_cleanup_never_stops_unmarked_services(monkeypatch):
    control = load_control()
    calls = []
    monkeypatch.setattr(control, 'cli', lambda args: calls.append(('cli', args)))
    monkeypatch.setattr(control, 'properties', lambda unit: {'LoadState': 'loaded', 'Environment': 'OTHER=1'})
    monkeypatch.setattr(control, 'native', lambda args, **kwargs: calls.append(('native', args)))
    control.cleanup()
    assert not any(kind == 'native' for kind, _ in calls)
    assert calls[0] == ('cli', ['restore', '--all'])


def test_failed_restoration_prevents_service_stops(monkeypatch):
    control = load_control()
    calls = []
    def fail(args):
        raise RuntimeError('Restoration failed')
    monkeypatch.setattr(control, 'cli', fail)
    monkeypatch.setattr(control, 'native', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError, match='Restoration failed'):
        control.cleanup()
    assert calls == []


def test_terminal_registry_is_separate_from_normal_and_web_demo_registry():
    control = load_control()
    assert control.DATABASE == ROOT/'var'/'terminal-demo'/'registry.sqlite3'
