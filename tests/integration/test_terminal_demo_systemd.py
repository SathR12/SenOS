"""Real terminal-demo startup, registration and ownership-checked cleanup."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(sys.platform != 'linux' or os.environ.get('SENOS_RUN_SYSTEMD_TESTS') != '1', reason='Opt-in systemd terminal demo test')
def test_owned_terminal_workloads_start_register_and_cleanup():
    spec = importlib.util.spec_from_file_location('terminal_demo_control_live', ROOT/'scripts/demo/control.py')
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    for unit in control.UNITS.values():
        if control.properties(unit).get('LoadState') not in (None, 'not-found'):
            pytest.skip('An existing terminal demo must not be disturbed')
    started = False
    try:
        # No interactive terminal in pytest: run the exact web command minus PTY/wait.
        args = [value for value in control.launch_args('web') if value not in ('--pty', '--wait')]
        subprocess.run(args, check=True, capture_output=True, text=True, timeout=15)
        started = True
        time.sleep(1)
        control.main('build')
        control.main('status')
        for unit in control.UNITS.values():
            props = control.properties(unit)
            assert props['ActiveState'] == 'active'
            assert control.owned(props)
            weight = control.native(['systemctl', '--user', 'show', unit, '-p', 'CPUWeight', '--value'])
            assert weight.stdout.strip() == '100'
    finally:
        if started:
            control.cleanup()
    for unit in control.UNITS.values():
        assert control.properties(unit).get('ActiveState') != 'active'
