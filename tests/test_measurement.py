import threading
import pytest

import src.measurement as measurement_mod
from src.measurement import SingleMeasurement


class FakeHW:
    def __init__(self):
        self.bus_lock = threading.Lock()

    def get_temp_probe(self):
        return None


class ScriptedProbe:
    """Returns scripted values; repeats the last one when exhausted."""
    def __init__(self, values):
        self.values = list(values)
        self.i = 0

    def read(self):
        v = self.values[min(self.i, len(self.values) - 1)]
        self.i += 1
        return v


def patch_settings(monkeypatch, settings):
    monkeypatch.setattr(measurement_mod, 'get_setting',
                        lambda key, default=None: settings.get(key, default))


def make_session(sensor):
    s = SingleMeasurement()
    s._sensor = sensor
    return s


class TestThresholds:
    def test_ph_absolute(self, monkeypatch):
        patch_settings(monkeypatch, {'STABILITY_WINDOW': 5,
                                     'PH_STABILITY_THRESHOLD': 0.02})
        window, fn = make_session('ph')._thresholds()
        assert window == 5
        assert fn(7.0) == 0.02
        assert fn(2.0) == 0.02  # independent of the mean

    def test_temp_absolute(self, monkeypatch):
        patch_settings(monkeypatch, {'STABILITY_WINDOW': 5,
                                     'TEMP_STABILITY_THRESHOLD': 0.1})
        _, fn = make_session('temp')._thresholds()
        assert fn(22.0) == pytest.approx(0.1)

    def test_ec_relative_with_floor(self, monkeypatch):
        patch_settings(monkeypatch, {'STABILITY_WINDOW': 5,
                                     'EC_STABILITY_THRESHOLD_PCT': 1.0})
        _, fn = make_session('ec')._thresholds()
        assert fn(1000.0) == pytest.approx(10.0)   # 1% of mean
        assert fn(10.0) == pytest.approx(1.0)      # absolute floor near zero


class TestPlateauDetection:
    """Run the real _run loop against scripted probes (fast intervals)."""

    def _run_session(self, monkeypatch, values, settings, duration=1.0):
        base = {'MEASUREMENT_POLL_INTERVAL': 0.01, 'MEASUREMENT_MIN_TIME': 0.0,
                'STABILITY_WINDOW': 5, 'PH_STABILITY_THRESHOLD': 0.02}
        base.update(settings)
        patch_settings(monkeypatch, base)
        monkeypatch.setattr(measurement_mod, 'get_hardware', lambda: FakeHW())
        s = make_session('ph')
        s._duration = duration
        s.running = True
        s._run(ScriptedProbe(values))
        return s.get_data()

    def test_true_plateau_detected(self, monkeypatch):
        d = self._run_session(monkeypatch, [7.00, 7.01, 7.00, 7.01, 7.00],
                              settings={}, duration=2.0)
        assert d['stable'] is True
        assert d['result'] == pytest.approx(7.005, abs=0.01)

    def test_slow_drift_not_called_stable(self, monkeypatch):
        # Monotonic +0.01/sample: std over 5 samples ~0.014 (below the 0.02
        # threshold, so std alone would declare stable) but the window drift
        # is ~0.03 - the drift criterion must reject it.
        drifting = [7.0 + 0.01 * i for i in range(300)]
        d = self._run_session(monkeypatch, drifting, settings={}, duration=0.6)
        assert d['stable'] is not True

    def test_min_time_gate_delays_plateau(self, monkeypatch):
        d = self._run_session(monkeypatch, [7.0] * 300,
                              settings={'MEASUREMENT_MIN_TIME': 0.3},
                              duration=2.0)
        assert d['stable'] is True
        assert d['stable_at'] >= 0.3


class TestLifecycle:
    def test_get_data_snapshot_shape(self):
        s = SingleMeasurement()
        d = s.get_data()
        for key in ('sensor', 'elapsed', 'values', 'running', 'stable',
                    'result', 'error'):
            assert key in d

    def test_double_start_rejected(self):
        s = SingleMeasurement()
        s.running = True
        with pytest.raises(RuntimeError):
            s.start('ec', 30)
