import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_commands():
    spec = importlib.util.spec_from_file_location('senos_shortcuts', ROOT/'scripts/commands.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shortcuts_keep_intent_as_one_argument_without_shell_evaluation():
    shortcuts = load_commands()
    text = 'Prioritize web; echo unsafe for one minute'
    assert shortcuts.cli_arguments('ask', {'INTENT': text}) == ['agent', 'run', text]
    assert shortcuts.cli_arguments('apply', {'INTENT': text}) == ['agent', 'run', text, '--watch', '--apply']


def test_register_and_restore_shortcuts_use_explicit_arguments():
    shortcuts = load_commands()
    assert shortcuts.cli_arguments('register', {'NAME': 'web', 'UNIT': 'web.service'}) == ['register', 'web', '--unit', 'web.service']
    assert shortcuts.cli_arguments('restore', {'NAME': 'web', 'DB': '/tmp/registry.sqlite3'}) == ['restore', 'web', '--db', '/tmp/registry.sqlite3']


def test_missing_name_does_not_accidentally_restore_everything():
    shortcuts = load_commands()
    with pytest.raises(ValueError, match='NAME'):
        shortcuts.cli_arguments('restore', {})


def test_demo_metadata_rejects_paths_outside_demo_storage(tmp_path):
    shortcuts = load_commands()
    state = tmp_path/'web-demo'
    state.mkdir()
    outside = tmp_path/'other'
    outside.mkdir()
    (state/'latest.json').write_text(__import__('json').dumps({'run': str(outside)}))
    with pytest.raises(ValueError, match='outside'):
        shortcuts.latest_run(state)
