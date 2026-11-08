"""Autoplay frame workload: real fixed CPU work, with a normal 30 FPS frame cap."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import multiprocessing as mp
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from benchmarks.workloads.cpu import cpu_work, publish

FPS = 30


def calibrate(target=.028):
    started = time.thread_time()
    cpu_work(20000)
    elapsed = time.thread_time()-started
    return max(1000, min(2000000, round(20000*target/max(elapsed, .000001))))


def position(seconds):
    # Reflected motion, computed in Python; the browser never interpolates frames.
    def bounce(value, width):
        return width-abs(value%(2*width)-width)
    return dict(x=30+bounce(seconds*600, 840), y=40+bounce(seconds*250, 320))


def frame_statistics(frames):
    values = [item['interval_ms'] for item in frames]
    elapsed = sum(values)/1000
    ordered = sorted(values)
    return dict(fps=len(values)/elapsed if elapsed > 0 else None,
        p95_frame_ms=ordered[max(0, math.ceil(len(ordered)*.95)-1)] if ordered else None,
        missed_deadlines=sum(value > 1000/FPS*1.10 for value in values), frames=len(values))


def serve(state):
    iterations = calibrate()
    context = mp.get_context('fork')
    shared = context.Array('d', 5+120*2)
    stop = context.Event()
    def engine():
        begun = previous = time.monotonic()
        sequence = 0
        while not stop.is_set():
            start = time.monotonic()
            cpu_work(iterations)
            # Ordinary frame pacing only; there is no phase-dependent fake delay.
            stop.wait(max(0, 1/FPS-(time.monotonic()-start)))
            now = time.monotonic()
            sequence += 1
            value = position(now-begun)
            with shared.get_lock():
                shared[0], shared[1], shared[2], shared[3] = sequence, value['x'], value['y'], now
                offset = 5+((sequence-1)%120)*2
                shared[offset], shared[offset+1] = sequence, (now-previous)*1000
            previous = now
    worker = context.Process(target=engine, daemon=True)
    def snapshot():
        with shared.get_lock():
            values = list(shared[:])
        sequence = int(values[0])
        recent = [dict(sequence=int(values[i]), interval_ms=values[i+1]) for i in range(5,len(values),2) if values[i]>0]
        recent.sort(key=lambda item:item['sequence'])
        return dict(sequence=sequence, x=values[1], y=values[2], recent_frames=recent,
            target_fps=FPS, calibrated_iterations=iterations, **frame_statistics(recent[-30:]))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ('/work', '/frame'):
                self.send_error(404)
                return
            body = b'ok\n' if self.path == '/work' else json.dumps(snapshot()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json' if self.path == '/frame' else 'text/plain')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args): pass
    with HTTPServer(('127.0.0.1', 0), Handler) as server:
        worker.start()
        publish(state, dict(port=server.server_port, iterations=iterations, target_fps=FPS))
        try:
            server.serve_forever(poll_interval=.1)
        finally:
            stop.set()
            worker.join(timeout=3)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=3)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('web',))
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--iterations', type=int, default=0, help='Compatibility with workload launcher; calibration fixes the frame work once at startup')
    serve(parser.parse_args().state)
