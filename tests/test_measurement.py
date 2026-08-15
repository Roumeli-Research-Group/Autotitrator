import pytest

import src.measurement as measurement_mod
from src.measurement import SingleMeasurement


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
