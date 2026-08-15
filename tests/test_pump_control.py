import random

import pytest

from src.pump_control import water_density, fit_pulse_trials


class TestWaterDensity:
    def test_table_values(self):
        assert water_density(25) == pytest.approx(0.99705)
        assert water_density(20) == pytest.approx(0.99821)

    def test_interpolation_between_points(self):
        # Midpoint between 24 (0.99730) and 25 (0.99705)
        assert water_density(24.5) == pytest.approx((0.99730 + 0.99705) / 2, abs=1e-5)

    def test_clamped_outside_range(self):
        assert water_density(0) == pytest.approx(0.99910)   # clamps to 15C
        assert water_density(90) == pytest.approx(0.99403)  # clamps to 35C


def synth_trials(Q=0.27, tau=0.042, rho=0.99705, noise=0.0, seed=1):
    """Trials from a simulated pump obeying v = Q*(t - tau)."""
    rng = random.Random(seed)
    sheet = [(0.185, 118), (0.278, 79), (0.556, 39), (1.111, 20), (2.222, 10), (4.444, 5)]
    trials = []
    for t, n in sheet:
        for _ in range(3):
            mass = rho * n * Q * (t - tau) * (1 + rng.gauss(0, noise))
            trials.append([t, n, mass])
    return trials


class TestFit:
    def test_recovers_q_and_tau_exactly_without_noise(self):
        fit = fit_pulse_trials(synth_trials(), temp_c=25.0)
        assert fit['Q'] == pytest.approx(0.27, abs=1e-6)
        assert fit['tau'] == pytest.approx(0.042, abs=1e-6)
        assert fit['r2'] == pytest.approx(1.0, abs=1e-9)
        assert all(fit['checks'].values())
        assert fit['warnings'] == []

    def test_recovers_with_realistic_noise(self):
        fit = fit_pulse_trials(synth_trials(noise=0.0005), temp_c=25.0)
        assert fit['Q'] == pytest.approx(0.27, rel=0.01)
        assert fit['tau'] == pytest.approx(0.042, rel=0.15)

    def test_zero_dead_time_pump(self):
        fit = fit_pulse_trials(synth_trials(tau=0.0), temp_c=25.0)
        assert fit['tau'] == pytest.approx(0.0, abs=1e-9)
        assert fit['checks']['tau_nonnegative']

    def test_mean_of_ratios_would_be_biased(self):
        # The whole reason for the fit: v/t averaging absorbs tau into Q
        trials = synth_trials()
        fit = fit_pulse_trials(trials, temp_c=25.0)
        rho = 0.99705
        ratios = [m / (rho * n) / t for t, n, m in trials]
        mean_of_ratios = sum(ratios) / len(ratios)
        # mean-of-ratios underestimates Q when tau > 0
        assert mean_of_ratios < fit['Q'] * 0.99

    def test_requires_three_trials(self):
        with pytest.raises(ValueError):
            fit_pulse_trials([[1.0, 10, 2.7], [2.0, 5, 2.7]], temp_c=25.0)

    def test_requires_two_distinct_on_times(self):
        with pytest.raises(ValueError):
            fit_pulse_trials([[1.0, 10, 2.7]] * 4, temp_c=25.0)

    def test_negative_tau_warned(self):
        # Pump that delivers extra volume per actuation (negative dead time)
        fit = fit_pulse_trials(synth_trials(tau=-0.05), temp_c=25.0)
        assert fit['tau'] < 0
        assert not fit['checks']['tau_nonnegative']
        assert any('negative' in w for w in fit['warnings'])

    def test_invalid_rows_ignored(self):
        trials = synth_trials()
        trials.append([0, 10, 5.0])    # zero on-time
        trials.append([1.0, 10, -1])   # negative mass
        fit = fit_pulse_trials(trials, temp_c=25.0)
        assert fit['Q'] == pytest.approx(0.27, abs=1e-6)

    def test_replicate_scatter_flagged(self):
        trials = synth_trials()
        trials[0][2] *= 1.05  # one rep off by 5%
        fit = fit_pulse_trials(trials, temp_c=25.0)
        assert not fit['checks']['replicates_ok']
