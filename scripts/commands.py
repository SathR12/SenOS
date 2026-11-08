"""Small, shell-free adapters for Make shortcuts and independent demo inspection."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT/'var'/'web-demo'

HELP = '''SenOS shortcuts (inside WSL/Linux)
  make demo                         AI web demo; real CPU changes on owned test services
  make demo-offline                 Same demo without API calls
  make demo-status                  Live SenOS telemetry for the latest demo
  make proof                        Independent Linux PID/cgroup/CPU counter evidence
  make results                      Latest saved phase results and background batches/s
  make demo-stop                    Safely stop the latest demo
  make status                       Your usual registered workloads
  make register NAME=web UNIT=web.service
  make remove NAME=web              Remove registration; leave the app running
  make ask TEXT="Prioritize web for one minute"       Dry-run AI advice
  make apply TEXT="Prioritize web for one minute"     Real CPU changes + restoration
  make prioritize NAME=web KEEP=build                 Direct CPU control, 60 seconds
  make restore NAME=web             Restore one workload
  make cli ARGS="status --json"     Any existing CLI options
  make test                         Unit tests
Optional DB=/path/to/registry.sqlite3 selects a custom registry for normal commands.
Demo commands automatically use their separate registry. Ctrl+C stops a foreground demo.
'''


def required(values, name):
    value = values.get(name, '').strip()
    if not value:
        raise ValueError(f'Provide {name}=...; run make help for examples')
    return value


def cli_arguments(action, values):
    if action == 'status':
        args = ['status']
    elif action == 'register':
        args = ['register', required(values, 'NAME'), '--unit', required(values, 'UNIT')]
    elif action in ('remove', 'restore'):
        args = ['unregister' if action == 'remove' else 'restore', required(values, 'NAME')]
    elif action in ('ask', 'apply'):
        text = values.get('TEXT', '').strip() or values.get('INTENT', '').strip()
        if not text:
            raise ValueError('Provide TEXT=...; run make help for examples')
        args = ['agent', 'run', text]
        if action == 'apply':
            args += ['--watch', '--apply']
    elif action == 'prioritize':
        args = ['monitor', 'start', required(values, 'NAME'), '--duration-seconds', values.get('DURATION') or '60', '--apply']
        if values.get('KEEP', '').strip():
            args += ['--preserve', values['KEEP'].strip()]
    elif action == 'cli':
        args = shlex.split(required(values, 'ARGS'))
    else:
        raise ValueError(f'Unknown shortcut: {action}')
    if values.get('DB', '').strip():
        args += ['--db', values['DB'].strip()]
    return args


def read_json(path):
    deadline = time.monotonic()+1
    while True:
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                raise ValueError(f'Cannot read {path.name}; start make demo first or check its terminal') from None
            time.sleep(.02)


def latest_run(state_root=STATE_ROOT):
    metadata = state_root/'latest.json'
    if metadata.is_file():
        run = Path(read_json(metadata)['run']).resolve()
    else:
        choices = list(state_root.glob('run-*'))
        if not choices:
            raise ValueError('No demo run found. Start make demo in another terminal first.')
        run = max(choices, key=lambda p: p.stat().st_ctime).resolve()
    if run.parent != state_root.resolve() or not run.name.startswith('run-'):
        raise ValueError('Demo metadata points outside its expected storage directory')
    if not run.is_dir():
        raise ValueError('Latest demo directory is missing; start make demo again')
    return run


def native(arguments):
    return subprocess.run(arguments, capture_output=True, text=True, timeout=10, check=False)


def cpu_counter(directory):
    fields = dict(line.split() for line in (directory/'cpu.stat').read_text().splitlines())
    return int(fields['usage_usec'])


def proof(run):
    units = read_json(run/'units.json')
    if not isinstance(units, list) or len(units) != 2 or any(not re.fullmatch(r'senos-bench-[0-9a-f]{32}\.service', unit) for unit in units):
        raise ValueError('Unexpected demo service identities; refusing to inspect arbitrary targets')
    print('Independent evidence from systemctl, /proc and cgroup v2 (not dashboard values).')
    print('Synthetic application, real Linux processes and real scheduler controls.\n')
    for label, unit in zip(('WEB', 'BACKGROUND'), units):
        arguments = ['systemctl', '--user', 'show', unit, '-p', 'Id', '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ControlGroup', '-p', 'CPUWeight', '-p', 'CPUUsageNSec']
        result = native(arguments)
        print(label+': '+shlex.join(arguments))
        print(result.stdout.strip() or result.stderr.strip())
        props = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if props.get('ActiveState') != 'active':
            print('Service is stopped. Start make demo to inspect a live process.\n')
            continue
        pid = int(props.get('MainPID', '0'))
        if pid <= 0:
            raise ValueError('Active service did not report a live PID')
        print('Process:', (Path('/proc')/str(pid)/'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace'))
        group = props.get('ControlGroup', '')
        if not group.startswith('/'):
            raise ValueError('Service did not report an absolute cgroup')
        directory = (Path('/sys/fs/cgroup')/group.lstrip('/')).resolve()
        if not directory.is_relative_to(Path('/sys/fs/cgroup')):
            raise ValueError('Unexpected cgroup path')
        print('Kernel cgroup:', directory)
        print('cpu.weight:', (directory/'cpu.weight').read_text().strip())
        first_time = time.monotonic()
        first = cpu_counter(directory)
        time.sleep(.5)
        last = cpu_counter(directory)
        elapsed = time.monotonic()-first_time
        print(f'cpu.stat usage_usec: {first} -> {last}; actual CPU over sample: {(last-first)/1_000_000/elapsed*100:.1f}% of one CPU')
        print('cpu.pressure:', (directory/'cpu.pressure').read_text().strip())
        print()
    print('Compare CPUWeight and kernel cpu.weight before/during/after: 100 -> 200 -> 100 for web; background stays 100.')
    print('For independent process monitoring: top -p PID (use MainPID printed above).')


def results(run):
    path = run/'summary.json'
    if not path.is_file():
        print(f'Results are still being collected. Run: {run}\nUse make demo-status and make proof while it runs.')
        return
    data = read_json(path)
    print(f"Run: {run}\nMode: {data['mode']} | Status: {data['status']} | Applied: {data['applied']} | Restored: {data['restored']}")
    print(f"{'PHASE':<15} {'MEAN ms':>10} {'p95 ms':>10} {'BG batches/s':>14} {'SPAN s':>9} {'ERRORS':>8}")
    def number(value):
        return 'N/A' if value is None else f'{value:.1f}'
    for phase in ('Baseline', 'Prioritized', 'Restored'):
        item = data['summary'].get(phase)
        if item:
            print(f"{phase:<15} {number(item['mean_ms']):>10} {number(item['p95_ms']):>10} {number(item.get('background_batches_per_second')):>14} {number(item.get('measurement_seconds')):>9} {item['errors']:>8}")
    print('Compare background batches/second, not totals from unequal-length phases. Older runs may have N/A rates.')
    for error in data['errors']:
        print('ERROR:', error)


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'help'
    if action == 'help':
        print(HELP)
        return
    if action in ('demo-status', 'proof', 'results', 'demo-stop'):
        run = latest_run()
        if action == 'proof':
            proof(run)
        elif action == 'results':
            results(run)
        elif action == 'demo-stop':
            (run/'STOP').touch()
            print('Stop requested. The foreground launcher verifies recovery and stops its owned services.')
        else:
            from senos.cli import app
            print(f'Live telemetry for demo run: {run}')
            app(args=['status', '--db', str(run/'registry.sqlite3')])
        return
    from senos.cli import app
    app(args=cli_arguments(action, os.environ))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f'SenOS shortcut: {error}', file=sys.stderr)
        raise SystemExit(1)
