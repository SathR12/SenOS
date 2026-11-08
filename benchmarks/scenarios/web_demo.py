"""Reproducible, bounded localhost demo on owned synthetic Linux workloads."""
import argparse
from collections import deque
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
STATE_ROOT = ROOT / 'var' / 'web-demo'
# Support `python /absolute/path/to/web_demo.py` from any directory as well.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.scenarios.cpu_contention import Services, command, preflight
from senos.workloads.registry import Registry


class DemoState:
    def __init__(self, mode):
        self.lock = threading.RLock()
        self.samples = deque(maxlen=180)
        self.phases = {}
        self.status = 'starting'
        self.stage = 'Starting'
        self.step = 1
        self.message = 'Checking Linux and preparing owned demo workloads.'
        self.mode = mode
        self.errors = []
        self.events = []
        self.applied = False
        self.restored = False
        self.controller_exit = None
        self.game = False
        self.game_port = None
        self.intent_text = 'Keep local-app responsive for one minute while background-build keeps progressing'
        self.accepting_intent = True
        self.app_port = None
        self.stage_deadline = None

    def update(self, **values):
        with self.lock:
            for name, value in values.items():
                setattr(self, name, value)

    def error(self, message):
        with self.lock:
            self.errors.append(message)
            self.message = message
            self.status = 'failed'
        print('ERROR: ' + message, flush=True)

    def set_intent(self, text):
        text = text.strip()
        if not text or len(text) > 500:
            return False, 'Enter an intent between 1 and 500 characters.'
        with self.lock:
            if self.mode != 'ai':
                return False, 'Custom intents require AI mode; deterministic mode uses the fixed rehearsal objective.'
            if not self.accepting_intent:
                return False, 'The agent has already started evaluating this run.'
            self.intent_text = text
        return True, 'Intent saved. The agent will evaluate it after the baseline measurement.'

    def try_app(self):
        with self.lock:
            port = self.app_port
        if port is None:
            return 409, {'ok': False, 'message': 'The local app is not ready. Try again when the demo is running.'}
        milliseconds, error = measure_workload_request(port)
        if error is not None:
            return 503, {'ok': False, 'message': f'The local app request failed: {error}'}
        return 200, {
            'ok': True,
            'milliseconds': milliseconds,
            'message': 'The local app responded. This personal try is not included in the measured comparison.',
        }

    def add_sample(self, sample):
        with self.lock:
            self.samples.append(sample)
            values = self.phases.setdefault(sample['phase'], [])
            values.append(sample)
            self.applied = self.applied or sample['phase'] == 'Prioritized'

    def controller_finished(self, code, *, applied, restored):
        with self.lock:
            self.controller_exit = code
            self.applied, self.restored = applied, restored
            self.status = 'failed' if code != 0 or not restored else ('completed' if applied else 'no_action')

    def snapshot(self):
        with self.lock:
            summary = {}
            for phase, items in self.phases.items():
                successes = [item['ms'] for item in items if item['error'] is None and item['ms'] is not None]
                ordered = sorted(successes)
                elapsed = items[-1].get('background_timestamp', items[-1]['timestamp']) - items[0].get('background_timestamp', items[0]['timestamp'])
                advanced = items[-1]['batches'] - items[0]['batches']
                summary[phase] = dict(successes=len(successes), errors=len(items)-len(successes),
                    mean_ms=statistics.mean(successes) if successes else None,
                    p95_ms=ordered[max(0, math.ceil(len(ordered)*.95)-1)] if ordered else None,
                    background_batches=max(0, advanced), measurement_seconds=elapsed if elapsed > 0 else None,
                    background_batches_per_second=advanced/elapsed if elapsed > 0 and advanced >= 0 else None,
                    cpu_weights=[list(pair) for pair in sorted({(item['web'], item['background']) for item in items})])
            for phase, items in self.phases.items():
                frames = [frame for item in items for frame in item.get('game_frames', [])]
                if frames:
                    from benchmarks.workloads.game import frame_statistics
                    summary[phase]['game'] = frame_statistics(frames)
            before = summary.get('Baseline', {}).get('mean_ms')
            after = summary.get('Prioritized', {}).get('mean_ms')
            reduction = (1-after/before)*100 if before and after is not None and self.status != 'failed' else None
            before_game = summary.get('Baseline', {}).get('game', {})
            after_game = summary.get('Prioritized', {}).get('game', {})
            fps_before, fps_after = before_game.get('fps'), after_game.get('fps')
            fps_change = (fps_after/fps_before-1)*100 if fps_before and fps_after and self.status != 'failed' else None
            if self.game:
                reduction = None  # Incidental HTTP probes do not measure game frame latency.
            stage_remaining = (
                max(0, math.ceil(self.stage_deadline-time.monotonic()))
                if self.stage_deadline is not None else None
            )
            return dict(game_fps_change_percent=fps_change, game=self.game, status=self.status, stage=self.stage, message=self.message, mode=self.mode,
                step=self.step,
                samples=list(self.samples), summary=summary, errors=list(self.errors), events=list(self.events),
                applied=self.applied, restored=self.restored, controller_exit=self.controller_exit,
                intent_text=self.intent_text, accepting_intent=self.accepting_intent,
                app_available=self.app_port is not None, stage_seconds_remaining=stage_remaining,
                latency_reduction_percent=reduction)


def controller_arguments(mode, run, intent_text=None, *, game=False, adaptive=False):
    target = 'demo-game' if game else 'local-app'
    background = 'demo-build' if game else 'background-build'
    default_intent = (f'Prioritize {target} for one minute while keeping {background} progressing' if game else
        'Keep local-app responsive for one minute while background-build keeps progressing')
    cli = [sys.executable, '-c', 'from senos.cli import app; app()']
    if mode == 'ai':
        cli += ['agent', 'run', intent_text or default_intent, '--watch']
        if adaptive:
            cli += ['--adaptive', '--interval', '10', '--max-cycles', '6']
    else:
        cli += ['monitor', 'start', target, '--duration-seconds', '60', '--preserve', background]
    return cli + ['--apply', '--json', '--db', str(run/'registry.sqlite3'), '--monitor-db', str(run/'monitor.sqlite3')]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return
        if self.path == '/game':
            port = self.server.demo.game_port
            if not self.server.demo.game or port is None:
                self.send_error(503, 'Game is warming up')
                return
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
            try:
                connection.request('GET', '/frame')
                response = connection.getresponse()
                body = response.read()
                self.send_response(response.status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (OSError, http.client.HTTPException):
                pass  # Closed tabs or a stopped owned game cannot alter CPU policy.
            finally:
                connection.close()
            return
        if self.path not in ('/', '/metrics'):
            self.send_error(404, 'Use / for the dashboard or /metrics for JSON')
            return
        data = self.server.demo.snapshot()
        body = ((Path(__file__).with_name('game_demo.html') if self.server.demo.game else Path(__file__).with_suffix('.html')).read_bytes() if self.path == '/' else json.dumps(data).encode())
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8' if self.path == '/' else 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # A closed browser tab is not a workload failure.

    def do_POST(self):
        if self.path == '/try-app':
            status, payload = self.server.demo.try_app()
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != '/intent':
            self.send_error(404, 'Use /intent to submit an objective or /try-app to try the local app')
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length > 4096:
                raise ValueError('Request is too large')
            payload = json.loads(self.rfile.read(length))
            text = payload.get('intent') if isinstance(payload, dict) else None
            if not isinstance(text, str):
                raise ValueError('Intent must be text')
            accepted, message = self.server.demo.set_intent(text)
            body = json.dumps({'accepted': accepted, 'message': message}).encode()
            self.send_response(200 if accepted else 409)
        except (ValueError, json.JSONDecodeError) as error:
            body = json.dumps({'accepted': False, 'message': str(error)}).encode()
            self.send_response(400)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def bind_server(port):
    try:
        return ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        return ThreadingHTTPServer(('127.0.0.1', 0), Handler)


def read_json(path, seconds=1):
    """DrvFS file replacement can expose a brief missing/partial-file window."""
    deadline = time.monotonic()+seconds
    while True:
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError) as error:
            if time.monotonic() >= deadline:
                raise RuntimeError(f'Cannot read demo state {path.name}; check the owned service journal') from error
            time.sleep(.02)


def ready_state(path, unit):
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        try:
            return read_json(path, seconds=.15)
        except RuntimeError:
            active = command(['systemctl', '--user', 'is-active', unit], check=False).stdout.strip()
            if active not in ('active', 'activating'):
                raise RuntimeError(f'Demo workload failed to start: {unit}; inspect journalctl --user -u {unit}') from None
    raise RuntimeError(f'Demo workload did not publish readiness within 15 seconds: {unit}')


def measure_workload_request(port):
    start = time.monotonic()
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('GET', '/work')
        response = connection.getresponse()
        body = response.read()
        if response.status != 200 or body != b'ok\n':
            raise RuntimeError('HTTP workload returned an unexpected response')
        return (time.monotonic()-start)*1000, None
    except (OSError, http.client.HTTPException, RuntimeError) as failure:
        return None, type(failure).__name__
    finally:
        connection.close()


def weights(units):
    values = []
    for unit in units:
        control_group = command(
            ['systemctl', '--user', 'show', unit, '-p', 'ControlGroup', '--value']
        ).stdout.strip()
        if not control_group:
            values.append(None)
            continue
        cgroup = (Path('/sys/fs/cgroup') / control_group.lstrip('/')).resolve()
        if not cgroup.is_relative_to(Path('/sys/fs/cgroup')) or cgroup == Path('/sys/fs/cgroup'):
            values.append(None)
            continue
        try:
            raw = (cgroup / 'cpu.weight').read_text().strip()
            values.append(int(raw) if raw.isdecimal() and 1 <= int(raw) <= 10000 else None)
        except (OSError, ValueError):
            values.append(None)
    return values


def recovery_verified(run):
    database = run/'registry.sqlite3'
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='cpu_execution_sessions'").fetchone()
        if not exists:
            return True  # No execution session ever began.
        return not connection.execute("SELECT 1 FROM cpu_execution_sessions WHERE status != 'restored'").fetchone()


def stop_controller(process):
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def experiment(state, run, cpu, mode, stop, done, game=False):
    services = Services(cpu, 300)
    controller = None
    units = []
    try:
        web = (services.start('web', run/'web.json', 400000, workload=ROOT/'benchmarks/workloads/game.py')
            if game else services.start('web', run/'web.json', 400000))
        ready = ready_state(run/'web.json', web)
        port = ready['port']
        if game:
            state.update(game_port=port)
            config = read_json(run/'configuration.json')
            config.update(game_calibrated_iterations=ready['iterations'], game_target_fps=ready['target_fps'], game_target_cpu_ms=28)
            (run/'configuration.json').write_text(json.dumps(config, indent=2)+'\n')
        background = services.start('background', run/'background.json', 20000)
        ready_state(run/'background.json', background)
        state.update(app_port=port)
        units = [web, background]
        (run/'units.json').write_text(json.dumps(units))
        registry = Registry(run/'registry.sqlite3')
        registry.register('demo-game' if game else 'local-app', web)
        registry.register('demo-build' if game else 'background-build', background)
        state.update(status='running', stage='Warmup', step=1, stage_deadline=time.monotonic()+10,
            message='Starting the local app and background build on one contended CPU.')
        if stop.wait(10):
            return
        baseline_start = time.monotonic()
        state.update(stage='Baseline', step=2, stage_deadline=baseline_start+15,
            message='Measuring how long the local app waits before SenOS helps. The background build continues.')
        controller_started = None
        restored_start = None
        boosted = False
        last_frame = 0
        if game:
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            try:
                connection.request('GET', '/frame')
                response = connection.getresponse()
                last_frame = json.loads(response.read())['sequence']
            finally:
                connection.close()
        with (run/'agent.log').open('w') as log, (run/'samples.jsonl').open('a') as samples_file:
            while not stop.is_set():
                now = time.monotonic()
                if controller is None and now-baseline_start >= 15:
                    state.update(accepting_intent=False)
                    state.update(stage='Resolving intent' if mode == 'ai' else 'Evaluating', step=3, stage_deadline=None,
                        message='SenOS is understanding your request; deterministic safety checks authorize any CPU change.' if mode == 'ai' else 'SenOS is evaluating the fixed offline objective with deterministic safety checks.')
                    controller = subprocess.Popen(controller_arguments(mode, run, state.intent_text, game=game, adaptive=getattr(state, "adaptive", False)), cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                    controller_started = now
                if controller is not None and controller.poll() is None and now-controller_started > 180:
                    raise RuntimeError('Controller exceeded 180 seconds; interrupting it and checking recovery')
                current = weights(units)
                if any(weight is None for weight in current):
                    raise RuntimeError('CPUWeight is unavailable for a live demo workload')
                if controller is not None and controller.poll() is not None:
                    if restored_start is None:
                        # Recheck the durable execution records as well as actual live settings.
                        restored = current == [100, 100] and recovery_verified(run)
                        state.controller_finished(controller.returncode, applied=boosted, restored=restored)
                        if controller.returncode != 0:
                            raise RuntimeError('Controller failed. Read agent.log in the printed run directory; no improvement is claimed.')
                        if not restored:
                            raise RuntimeError('Restoration could not be verified; pending recovery needs review')
                        restored_start = now
                        state.update(stage='Restored' if boosted else 'No action', step=5, stage_deadline=now+10,
                            message='Back to normal: SenOS verified the original priorities. Measuring for 10 more seconds.' if boosted else 'SenOS made no verified change. This run does not claim an improvement.')
                    if now-restored_start >= 10:
                        break
                phase = ('Restored' if restored_start is not None and boosted else
                    'Prioritized' if current[0] > 100 else 'Resolving' if controller is not None else 'Baseline')
                if current[1] != 100 or current[0] not in (100, 200):
                    raise RuntimeError('Unexpected CPU settings; refusing to label measurements as valid')
                ms, error = measure_workload_request(port)
                after = weights(units)
                # A policy transition during a request is shown but excluded from phase comparisons.
                if after != current:
                    phase = 'Transition'
                background_progress = read_json(run/'background.json')
                batches = background_progress['completed_batches']
                sample = dict(timestamp=time.monotonic(), ms=ms, error=error, web=after[0], background=after[1], phase=phase, batches=batches, background_timestamp=background_progress['monotonic'])
                if game:
                    frame_connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                    try:
                        frame_connection.request('GET', '/frame')
                        reply = frame_connection.getresponse()
                        if reply.status != 200:
                            raise RuntimeError('Game frame endpoint failed')
                        frame = json.loads(reply.read())
                    finally:
                        frame_connection.close()
                    sample['game_frames'] = [item for item in frame.get('recent_frames', []) if item['sequence'] > last_frame]
                    last_frame = frame['sequence']
                state.add_sample(sample)
                samples_file.write(json.dumps(sample)+'\n')
                samples_file.flush()
                boosted = boosted or phase == 'Prioritized'
                if phase == 'Baseline':
                    state.update(stage=phase)
                elif phase == 'Prioritized':
                    state.update(stage=phase, step=4)
                # Only closed CLI JSON events are included; no environment or provider secrets are read.
                events = []
                for line in (run/'agent.log').read_text().splitlines():
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict):
                            events.append(value)
                    except json.JSONDecodeError:
                        pass
                state.update(events=events[-12:])
                stop.wait(0.5)
        if stop.is_set():
            state.update(status='stopped', message='Demo stopped; cleanup and recovery checks are running.')
    except Exception as error:
        state.error(f'{type(error).__name__}: {error}')
    finally:
        if stop.is_set() and state.status != 'failed':
            state.update(status='stopped', message='Stopped by the user; recovery and workload cleanup were checked.')
        try:
            stop_controller(controller)
            if controller is not None and units and (run/'registry.sqlite3').exists():
                # Independent recovery handles abnormal controller termination. Allow its polling interval.
                until = time.monotonic()+12
                while not recovery_verified(run) and time.monotonic() < until:
                    time.sleep(1)
                live_weights = weights(units)
                restored = live_weights in ([100, 100], [None, None]) and recovery_verified(run)
                state.update(restored=restored)
                if not restored:
                    state.error('CPU restoration not verified before workload shutdown; inspect registry recovery records.')
        except Exception as error:
            state.error(f'Recovery check failed: {type(error).__name__}: {error}')
        finally:
            errors = services.cleanup()
            state.update(app_port=None)
            for error in errors:
                state.error(error)
            state.update(stage='Finished', stage_deadline=None,
                message=state.message if state.status in ('failed', 'no_action', 'stopped') else 'Completed: original settings verified, demo services stopped. Results are saved below.')
            try:
                (run/'summary.json').write_text(json.dumps(state.snapshot(), indent=2)+'\n')
            except OSError as error:
                state.error(f'Could not save summary: {type(error).__name__}')
            result = state.snapshot()
            print('RESULT: '+json.dumps({key: result[key] for key in ('status','summary','applied','restored','latency_reduction_percent','game_fps_change_percent','errors')}), flush=True)
            print(f'Artifacts: {run}', flush=True)
            done.set()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adaptive', action='store_true', help='Use periodic LLM numeric weight selection in AI mode')
    parser.add_argument('--game', action='store_true', help='Autoplay Python game with measured frame deadlines')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--mode', choices=('ai', 'deterministic'), default='ai')
    parser.add_argument('--auto-ai', action='store_true', help='Compatibility alias for --mode ai; AI mode starts automatically')
    parser.add_argument('--exit-after-run', action='store_true', help='Stop the dashboard after saving results')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('port must be between 0 and 65535')
    if args.auto_ai:
        args.mode = 'ai'
    if sys.platform != 'linux':
        parser.error('Run this demo inside WSL/Linux, not Windows Python')
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (STATE_ROOT/'demo.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('A SenOS web demo is already running. Open the URL printed in its terminal or stop it with Ctrl+C before rerunning.', file=sys.stderr)
            return 2
        cpu = preflight(None)
        if args.mode == 'ai':
            from senos.agents.environment import load_agent_env
            from senos.agents.provider import create_provider
            from senos.agents.provider import AgentError
            try:
                load_agent_env(ROOT/'.env')
                create_provider()  # Validate local configuration without an API call.
            except AgentError as error:
                parser.error(f'AI setup: {error}. Use --mode deterministic for an offline run.')
        if not Path(__file__).with_suffix('.html').is_file():
            parser.error('Demo dashboard file is missing; restore benchmarks/scenarios/web_demo.html')
        # Bind before launching any workload or paying for an API call.
        with bind_server(args.port) as server:
            state = DemoState(args.mode)
            state.update(game=args.game)
            if args.game:
                state.update(intent_text='Prioritize demo-game for one minute while keeping demo-build progressing')
            server.demo = state
            server.timeout = .5
            run = Path(tempfile.mkdtemp(prefix='run-', dir=STATE_ROOT))
            stop, done = threading.Event(), threading.Event()
            print(f'OPEN http://localhost:{server.server_port}', flush=True)
            if args.port and server.server_port != args.port:
                print(f'Port {args.port} was occupied; selected {server.server_port} before starting workloads.', flush=True)
            print(f'RUN DATA: {run}', flush=True)
            print(f'MODE: {args.mode}. Warmup 10s -> baseline 15s -> one-minute objective -> restored 10s.', flush=True)
            print('Ctrl+C stops the demo safely. No manual registry paths or second CLI command are needed.', flush=True)
            (run/'configuration.json').write_text(json.dumps(dict(mode=args.mode, game=args.game, python=sys.version,
                kernel=os.uname().release, workload_cpu=cpu, allowed_cpus=sorted(os.sched_getaffinity(0)),
                web_iterations=400000, background_iterations=20000, baseline_seconds=15,
                intent_seconds=60, restored_seconds=10, dashboard_port=server.server_port), indent=2)+'\n')
            temporary = STATE_ROOT/'latest.tmp'
            temporary.write_text(json.dumps(dict(run=str(run), game=args.game, url=f'http://localhost:{server.server_port}'))+'\n')
            temporary.replace(STATE_ROOT/'latest.json')
            print('Other terminal commands: make demo-status, make proof, make results', flush=True)
            state.adaptive = args.adaptive
            worker = threading.Thread(target=experiment, args=(state, run, cpu, args.mode, stop, done, args.game))
            worker.start()
            finished_at = None
            try:
                while not (run/'STOP').exists():
                    server.handle_request()
                    if done.is_set():
                        finished_at = finished_at or time.monotonic()
                        if args.exit_after_run or time.monotonic()-finished_at > 300:
                            break
            except KeyboardInterrupt:
                print('Stopping demo and verifying recovery...', flush=True)
            finally:
                stop.set()
                worker.join(timeout=60)
                if worker.is_alive():
                    state.error('Cleanup is still running; inspect the run directory and systemd recovery service.')
            return 1 if state.snapshot()['status'] == 'failed' else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Demo startup failed: {error}', file=sys.stderr)
        raise SystemExit(1)
