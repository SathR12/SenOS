from benchmarks.workloads.game import frame_statistics, position
from benchmarks.scenarios.web_demo import controller_arguments


def test_python_animation_stays_in_bounds_without_user_input():
    for seconds in (0, .01, 1, 10, 1000):
        value = position(seconds)
        assert 30 <= value['x'] <= 870 and 40 <= value['y'] <= 360
    assert position(0) != position(1)


def test_frame_metrics_measure_real_intervals_and_missed_deadlines():
    frames = [dict(sequence=i, interval_ms=50) for i in range(20)]
    assert frame_statistics(frames) == dict(fps=20, p95_frame_ms=50, missed_deadlines=20, frames=20)
    assert frame_statistics([])['fps'] is None


def test_game_controller_targets_the_registered_game(tmp_path):
    args = controller_arguments('deterministic', tmp_path, game=True)
    assert args[5] == 'demo-game'
    assert '--apply' in args and '--duration-seconds' in args
    ai = controller_arguments('ai', tmp_path, game=True)
    assert 'Prioritize demo-game for one minute while keeping demo-build progressing' in ai


def test_game_phase_summary_reports_frames_not_http_probe_latency():
    from benchmarks.scenarios.web_demo import DemoState
    state = DemoState('deterministic')
    state.update(game=True)
    for phase, interval, sequence, weight in [('Baseline', 50, 1, 100), ('Prioritized', 40, 2, 200)]:
        state.add_sample(dict(phase=phase, timestamp=sequence, ms=1, batches=sequence,
            web=weight, background=100, error=None,
            game_frames=[dict(sequence=sequence, interval_ms=interval)]))
    result = state.snapshot()
    assert result['summary']['Baseline']['game']['fps'] == 20
    assert result['summary']['Prioritized']['game']['fps'] == 25
    assert result['game_fps_change_percent'] == 25
    assert result['latency_reduction_percent'] is None
