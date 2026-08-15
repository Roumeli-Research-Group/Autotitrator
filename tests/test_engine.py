import logging

import pytest

import src.titration.engine as engine_mod
from src.titration.engine import (compute_pump_on_time,
                                  check_step_against_calibration,
                                  interpolate_crossing)


def patch_settings(monkeypatch, settings):
    monkeypatch.setattr(engine_mod, 'get_setting',
                        lambda key, default=None: settings.get(key, default))


class TestComputePumpOnTime:
    def test_calibrated_q_and_tau(self, monkeypatch):
        patch_settings(monkeypatch, {'PUMP_FLOW_RATE_MLS': 0.27,
                                     'PUMP_DEAD_TIME_S': 0.042})
        t_on, q, tau = compute_pump_on_time(0.5)
        assert t_on == pytest.approx(0.5 / 0.27 + 0.042)
        assert q == 0.27
        assert tau == 0.042

    def test_falls_back_to_legacy_flow_rate(self, monkeypatch):
        patch_settings(monkeypatch, {'PUMP_FLOW_RATE_MLS': None,
                                     'PUMP_DEAD_TIME_S': None,
                                     'DEFAULT_FLOW_RATE': 0.2245})
        t_on, q, tau = compute_pump_on_time(0.5)
        assert q == 0.2245
        assert tau == 0.0
        assert t_on == pytest.approx(0.5 / 0.2245)

    def test_explicit_flow_rate_overrides(self, monkeypatch):
        patch_settings(monkeypatch, {'PUMP_FLOW_RATE_MLS': 0.27,
                                     'PUMP_DEAD_TIME_S': 0.042})
        t_on, q, tau = compute_pump_on_time(0.5, flow_rate=0.5)
        assert q == 0.5
        assert t_on == pytest.approx(1.0 + 0.042)

    def test_zero_flow_rate_gives_zero_on_time(self, monkeypatch):
        patch_settings(monkeypatch, {'PUMP_FLOW_RATE_MLS': None,
                                     'PUMP_DEAD_TIME_S': None,
                                     'DEFAULT_FLOW_RATE': 0})
        t_on, q, tau = compute_pump_on_time(0.5)
        assert t_on == 0.0


class TestStepCalibrationGuard:
    def test_warns_on_step_mismatch_without_tau(self, monkeypatch, caplog):
        patch_settings(monkeypatch, {'PUMP_CAL_STEP_VOLUME_ML': 0.1,
                                     'PUMP_DEAD_TIME_S': None})
        with caplog.at_level(logging.WARNING):
            check_step_against_calibration(0.5, 0.0, 2.0)
        assert any('step-specific' in r.message for r in caplog.records)

    def test_silent_when_step_matches(self, monkeypatch, caplog):
        patch_settings(monkeypatch, {'PUMP_CAL_STEP_VOLUME_ML': 0.5,
                                     'PUMP_DEAD_TIME_S': None})
        with caplog.at_level(logging.WARNING):
            check_step_against_calibration(0.5, 0.0, 2.0)
        assert caplog.records == []

    def test_silent_on_mismatch_with_fitted_tau(self, monkeypatch, caplog):
        # With tau fitted, the model generalizes across step sizes
        patch_settings(monkeypatch, {'PUMP_CAL_STEP_VOLUME_ML': 0.1,
                                     'PUMP_DEAD_TIME_S': 0.04})
        with caplog.at_level(logging.WARNING):
            check_step_against_calibration(0.5, 0.04, 2.0)
        assert caplog.records == []

    def test_warns_when_tau_dominates_on_time(self, monkeypatch, caplog):
        patch_settings(monkeypatch, {'PUMP_CAL_STEP_VOLUME_ML': 0.5,
                                     'PUMP_DEAD_TIME_S': 0.05})
        with caplog.at_level(logging.WARNING):
            check_step_against_calibration(0.02, 0.05, 0.12)
        assert any('dead time' in r.message for r in caplog.records)


class TestInterpolateCrossing:
    def test_midpoint(self):
        # pH went 6.0 -> 8.0 between 10 and 11 mL; target 7.0 is halfway
        assert interpolate_crossing(10.0, 6.0, 11.0, 8.0, 7.0) == pytest.approx(10.5)

    def test_descending_ph(self):
        assert interpolate_crossing(5.0, 8.0, 5.5, 6.0, 7.0) == pytest.approx(5.25)

    def test_flat_segment_returns_last(self):
        assert interpolate_crossing(10.0, 7.0, 11.0, 7.0, 7.0) == 11.0

    def test_clamped_inside_segment(self):
        # Target outside the segment: clamp rather than extrapolate
        v = interpolate_crossing(10.0, 6.0, 11.0, 6.5, 7.0)
        assert 10.0 <= v <= 11.0
