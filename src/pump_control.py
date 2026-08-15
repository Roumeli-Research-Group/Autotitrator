
"""
Pump calibration support: pulse-train driver, water density, and the
two-constant gravimetric fit  v_pulse = Q * t_on - Q * tau.

The pump is relay-switched and dosed by time. Delivered volume per actuation
is V = Q * (t_on - tau), where tau is the per-actuation dead time (relay
pull-in, pump spin-up, line pressurisation). tau is negligible over long runs
and dominant for sub-second injections, so it must be fitted, not assumed zero.
"""

import time
import threading
import logging
import numpy as np
from src.hardware import get_hardware

logger = logging.getLogger(__name__)


# --- Water density -----------------------------------------------------------
# g/mL vs temperature in Celsius (standard values; matches the calibration doc)
_DENSITY_T = [15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25,
              26, 27, 28, 29, 30, 31, 32, 33, 34, 35]
_DENSITY_R = [0.99910, 0.99895, 0.99878, 0.99860, 0.99841, 0.99821, 0.99799,
              0.99777, 0.99754, 0.99730, 0.99705, 0.99679, 0.99652, 0.99626,
              0.99597, 0.99565, 0.99534, 0.99503, 0.99470, 0.99437, 0.99403]

def water_density(temp_c):
    """Density of water (g/mL) at temp_c, linearly interpolated, clamped 15-35."""
    return float(np.interp(temp_c, _DENSITY_T, _DENSITY_R))


# --- Pulse train runner ------------------------------------------------------
class PulseTrainRunner:
    """Fires the relay N times at a fixed on-time with a fixed gap.

    Drives the pump with explicit on-times: the software's volume-to-time
    conversion must NOT be used here, because that conversion is the thing
    being calibrated.
    """

    def __init__(self):
        self.running = False
        self._stop_signal = False
        self._lock = threading.Lock()
        self._fired = 0
        self._total = 0
        self._on_time = 0.0
        self._gap = 0.0
        self._error = None

    def start(self, on_time, pulses, gap):
        with self._lock:
            if self.running:
                raise RuntimeError("Pulse train already running")
            self.running = True
            self._stop_signal = False
            self._fired = 0
            self._total = pulses
            self._on_time = on_time
            self._gap = gap
            self._error = None

        thread = threading.Thread(
            target=self._run, args=(on_time, pulses, gap), daemon=True)
        thread.start()

    def stop(self):
        self._stop_signal = True

    def _run(self, on_time, pulses, gap):
        pump = get_hardware().get_pump()
        logger.info(f"Pulse train: {pulses} x {on_time:.3f}s (gap {gap:.2f}s)")
        try:
            for i in range(pulses):
                if self._stop_signal:
                    logger.info(f"Pulse train stopped at {i}/{pulses}")
                    break
                pump.start()
                time.sleep(on_time)   # not chunked: on-time precision matters
                pump.stop()
                with self._lock:
                    self._fired = i + 1
                if i < pulses - 1 and gap > 0:
                    # Interruptible gap wait
                    t_end = time.monotonic() + gap
                    while time.monotonic() < t_end and not self._stop_signal:
                        time.sleep(min(0.1, t_end - time.monotonic()))
        except Exception as e:
            logger.error(f"Pulse train error: {e}")
            with self._lock:
                self._error = str(e)
        finally:
            try:
                pump.stop()
            except Exception:
                pass
            self.running = False

    def get_status(self):
        with self._lock:
            return {
                'running': self.running,
                'fired': self._fired,
                'total': self._total,
                'on_time': self._on_time,
                'gap': self._gap,
                'error': self._error,
            }


# --- Stage 2 fit -------------------------------------------------------------
def fit_pulse_trials(trials, temp_c):
    """
    Least-squares fit of the duration series.

    trials: list of [on_time_s, n_pulses, mass_g] (each replicate is one row)
    Returns dict with Q (mL/s), tau (s), r2, residual RMS (uL), per-point
    volumes, replicate SD per on-time, and quality-check warnings.

    Fits v_pulse = Q * t + b  =>  slope Q, x-intercept tau = -b/Q.
    Never average v/t ratios: that assumes b == 0, which discards the
    quantity being measured.
    """
    rho = water_density(temp_c)

    clean = []
    for row in trials:
        t, n, m = float(row[0]), int(row[1]), float(row[2])
        if t <= 0 or n <= 0 or m <= 0:
            continue
        clean.append((t, n, m, m / (rho * n)))  # v_pulse in mL

    if len(clean) < 3:
        raise ValueError("Need at least 3 valid trials to fit")
    if len(set(t for t, *_ in clean)) < 2:
        raise ValueError("Need at least 2 distinct on-times to fit")

    x = np.array([c[0] for c in clean])
    y = np.array([c[3] for c in clean])

    slope, intercept = np.polyfit(x, y, 1)
    if slope <= 0:
        raise ValueError("Fit produced non-positive flow rate; check the data")

    Q = float(slope)
    tau = float(-intercept / slope)
    if abs(tau) < 1e-9:  # numerical noise around a true zero dead time
        tau = 0.0

    pred = slope * x + intercept
    resid = y - pred
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    resid_rms_ul = float(np.sqrt(np.mean(resid ** 2))) * 1000.0

    # Replicate scatter within each on-time group
    groups = {}
    for t, n, m, v in clean:
        groups.setdefault(round(t, 4), []).append(v)
    replicates = []
    worst_rep_sd_pct = 0.0
    for t, vols in sorted(groups.items()):
        mean_v = float(np.mean(vols))
        sd_pct = (float(np.std(vols)) / mean_v * 100.0) if len(vols) > 1 and mean_v > 0 else 0.0
        worst_rep_sd_pct = max(worst_rep_sd_pct, sd_pct)
        replicates.append({'on_time': t, 'n': len(vols),
                           'mean_v_pulse': mean_v, 'sd_pct': round(sd_pct, 3)})

    min_v = float(np.min(y))
    checks = {
        'r2_ok': r2 > 0.999,
        'resid_ok': resid_rms_ul < 0.01 * min_v * 1000.0,
        'replicates_ok': worst_rep_sd_pct < 0.5,
        'tau_nonnegative': tau >= 0,
    }
    warnings = []
    if not checks['r2_ok']:
        warnings.append(f"R^2 = {r2:.5f} (< 0.999): look for a bad row or a bubble.")
    if not checks['resid_ok']:
        warnings.append(
            f"Residual RMS {resid_rms_ul:.2f} uL exceeds 1% of the smallest "
            f"pulse ({min_v * 10:.2f} uL): re-run the offending on-time.")
    if not checks['replicates_ok']:
        warnings.append(
            f"Worst replicate SD {worst_rep_sd_pct:.2f}% (> 0.5%): weighing or bubble problem.")
    if tau < 0:
        warnings.append(
            "tau fitted negative: likely over-fitting noise. The pump has no "
            "measurable dead time; consider using Q alone (tau = 0).")

    return {
        'Q': Q,
        'tau': tau,
        'tau_ms': tau * 1000.0,
        'r2': r2,
        'resid_rms_ul': resid_rms_ul,
        'rho': rho,
        'temp_c': temp_c,
        'points': [{'on_time': c[0], 'pulses': c[1], 'mass_g': c[2],
                    'v_pulse_ml': c[3]} for c in clean],
        'replicates': replicates,
        'checks': checks,
        'warnings': warnings,
    }
