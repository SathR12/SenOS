"""Bounded terminal-demo launcher; never stop generic user web/build services."""
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT/'var'/'terminal-demo'/'registry.sqlite3'
APP = Path(__file__).with_name('demo_app.py')
UNITS = {'web': 'senos-terminal-web.service', 'build': 'senos-terminal-build.service'}
MARKER = 'SENOS_TERMINAL_DEMO_ROOT'


def native(args, *, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=10)


def properties(unit):
    result = native(['systemctl', '--user', 'show', unit, '-p', 'LoadState', '-p', 'ActiveState', '-p', 'Environment'], check=False)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def owned(props):
    return f'{MARKER}={ROOT}' in shlex.split(props.get('Environment', ''))


def launch_args(kind):
    unit = UNITS[kind]
    return ['systemd-run', '--user', '--collect', '--unit', unit,
        '--property=CPUWeight=100', '--property=RuntimeMaxSec=1800',
        f'--setenv={MARKER}={ROOT}', f'--setenv=DEMO_WORKERS={3 if kind == "web" else 8}',
        *(['--pty', '--wait'] if kind == 'web' else []), sys.executable, '-u', str(APP)]


def cli(args):
    subprocess.run([sys.executable, '-c', 'from senos.cli import app; app()', *args, '--db', str(DATABASE)], cwd=ROOT, check=True)


def cleanup():
    # Recovery is verified before stopping owned services. Failure remains visible.
    cli(['restore', '--all'])
    for unit in UNITS.values():
        props = properties(unit)
        if props.get('LoadState') == 'not-found':
            continue
        if not owned(props):
            print(f'Skipping {unit}: it is not marked as this checkout\'s terminal demo.')
            continue
        native(['systemctl', '--user', 'stop', unit])
        native(['systemctl', '--user', 'reset-failed', unit], check=False)
    cli(['unregister', '--all', '--yes'])
    print('Owned terminal demo services stopped; demo registrations removed.')


def main(action):
    if sys.platform != 'linux':
        raise ValueError('Run inside WSL/Linux')
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    if action == 'cleanup':
        cleanup()
        return
    if action in ('status', 'ai', 'apply'):
        if action == 'status':
            cli(['status'])
        elif action == 'ai':
            cli(['agent', 'run', 'Prioritize web for one minute while keeping build progressing', '--watch', '--apply'])
        else:
            cli(['monitor', 'start', 'web', '--duration-seconds', '60', '--preserve', 'build', '--apply'])
        return
    kind = 'web' if action == 'app' else 'build'
    props = properties(UNITS[kind])
    if props.get('LoadState') not in (None, 'not-found'):
        raise ValueError(f'{UNITS[kind]} already exists; run make terminal-cleanup after reviewing ownership')
    if kind == 'build':
        web = properties(UNITS['web'])
        if not owned(web) or web.get('ActiveState') != 'active':
            raise ValueError('Start make terminal-app in terminal 1 first')
    try:
        subprocess.run(launch_args(kind), cwd=ROOT, check=True)
        if kind == 'build':
            cli(['register', 'web', '--unit', UNITS['web']])
            cli(['register', 'build', '--unit', UNITS['build']])
            print('Wait about 10 seconds, then make terminal-status or make terminal-ai.')
    except KeyboardInterrupt:
        if owned(properties(UNITS[kind])):
            native(['systemctl', '--user', 'stop', UNITS[kind]])
    except Exception:
        if kind == 'build' and owned(properties(UNITS[kind])):
            native(['systemctl', '--user', 'stop', UNITS[kind]])
        raise


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in ('app', 'build', 'cleanup', 'status', 'ai', 'apply'):
            raise ValueError('Choose app, build, status, ai, apply, or cleanup')
        main(sys.argv[1])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Terminal demo: {error}', file=sys.stderr)
        raise SystemExit(1)
