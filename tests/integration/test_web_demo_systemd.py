"""Opt-in repeated lifecycle check; no model calls or API credits."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(sys.platform != 'linux' or os.environ.get('SENOS_RUN_SYSTEMD_TESTS') != '1', reason='Opt-in Linux/systemd demo check')
def test_two_serial_web_demos_restore_and_cleanup(tmp_path):
    for _ in range(2):
        result = subprocess.run([sys.executable, str(ROOT/'benchmarks/scenarios/web_demo.py'),
            '--mode', 'deterministic', '--port', '0', '--exit-after-run'], cwd=tmp_path,
            text=True, capture_output=True, timeout=240)
        assert result.returncode == 0, result.stdout + result.stderr
        directory = next(line.removeprefix('RUN DATA: ') for line in result.stdout.splitlines() if line.startswith('RUN DATA: '))
        run = Path(directory)
        summary = json.loads((run/'summary.json').read_text())
        assert summary['status'] in ('completed', 'no_action')
        assert summary['restored'] is True and summary['errors'] == []
        assert summary['summary']['Baseline']['successes'] > 0
        assert all(phase['errors'] == 0 for phase in summary['summary'].values())
        for unit in json.loads((run/'units.json').read_text()):
            active = subprocess.run(['systemctl', '--user', 'is-active', unit], capture_output=True, text=True)
            assert active.stdout.strip() not in ('active', 'activating'), unit
