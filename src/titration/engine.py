
import time
import os
import datetime
import threading
import copy
import pandas as pd
import logging
import numpy as np
from src.hardware import get_hardware
from src.utils import get_setting

# Configure logging
logger = logging.getLogger(__name__)


def compute_pump_on_time(volume_ml, flow_rate=None):
    """
    Relay on-time for a dose of volume_ml:  t_on = V / Q + tau.

    Q is the true volumetric rate while running; tau the per-actuation dead
    time (relay pull-in, spin-up, line pressurisation) charged every time the
    relay fires. Values come from pump calibration (PUMP_FLOW_RATE_MLS /
    PUMP_DEAD_TIME_S), falling back to the legacy DEFAULT_FLOW_RATE with
    tau = 0. Returns (t_on, Q, tau).
    """
    q = flow_rate or get_setting('PUMP_FLOW_RATE_MLS') \
        or get_setting('DEFAULT_FLOW_RATE', 0.2245)
    q = float(q)
    tau = float(get_setting('PUMP_DEAD_TIME_S') or 0.0)
    t_on = (volume_ml / q if q > 0 else 0.0) + tau
    return t_on, q, tau


def check_step_against_calibration(step_volume, tau, t_on):
    """
    Warn when the requested step is outside what the calibration supports.

    - With no fitted dead time, the stored rate is only valid at the step
      size it was derived for (Q and tau collapse into one effective rate).
    - With a fitted dead time, warn when tau dominates the on-time: that
      regime delivers far less than predicted and is unusable.
    """
    cal_step = get_setting('PUMP_CAL_STEP_VOLUME_ML')
    dead_time_fitted = get_setting('PUMP_DEAD_TIME_S') is not None

    if cal_step and not dead_time_fitted and abs(step_volume - float(cal_step)) > 1e-9:
        logger.warning(
            f"Step volume {step_volume} mL differs from the calibrated step "
            f"{cal_step} mL and no pump dead time is stored. The stored flow "
            f"rate is step-specific: dosing may be biased. Run a full pump "
            f"calibration or match the calibrated step size.")

    if dead_time_fitted and t_on > 0 and tau > 0.2 * t_on:
        logger.warning(
            f"Pump dead time ({tau * 1000:.0f} ms) is more than 20% of the "
            f"computed on-time ({t_on:.3f} s). Short-pulse dosing in this "
            f"regime is unreliable; use a larger step volume.")


def interpolate_crossing(v_prev, r_prev, v_last, r_last, target):
    """
    Linear interpolation of the volume at which the reading crossed `target`,
    given the last point before the crossing and the first point after it.
    Returns v_prev..v_last clamped; if the two readings are equal, returns
    v_last (no slope information).
    """
    if r_last == r_prev:
        return v_last
    frac = (target - r_prev) / (r_last - r_prev)
    frac = max(0.0, min(1.0, frac))
    return v_prev + frac * (v_last - v_prev)


class TitrationEngine:
    def __init__(self):
        self.running = False
        self.stop_signal = False
        self.hw = get_hardware()
        self._data_lock = threading.Lock()  # Thread safety for shared state
        self._current_data = {}  # Shared state for graph
        self._last_ec = 0.0  # Cached probe readings for status endpoint
        self._last_ph = 0.0
        self.error = None        # last run's failure reason, surfaced via API
        self.last_result = None  # summary of the last completed run

    @property
    def current_data(self):
        """Thread-safe accessor for current data."""
        with self._data_lock:
            return copy.deepcopy(self._current_data)

    @current_data.setter
    def current_data(self, value):
        """Thread-safe setter for current data."""
        with self._data_lock:
            self._current_data = value

    def _update_data(self, key, value):
        """Thread-safe update of a single data key."""
        with self._data_lock:
            if key not in self._current_data:
                self._current_data[key] = []
            self._current_data[key].append(value)

    def stop(self):
        self.stop_signal = True
        if self.running:
           logger.info("Titration stop signal received.")

    def _fail(self, message):
        """Record a run failure so the UI can display it, then raise."""
        self.error = message
        logger.error(f"Titration failed: {message}")
        raise RuntimeError(message)

    def _apply_temp_compensation(self, probe):
        """Read the RTD probe (if present) and push T,<temp> to the measuring
        probe so the EZO firmware compensates its readings. Returns the
        temperature used, or None."""
        temp_probe = self.hw.get_temp_probe()
        if not temp_probe or probe is temp_probe:
            return None
        try:
            temp = temp_probe.read()
        except Exception as e:
            logger.warning(f"Temperature read for compensation failed: {e}")
            return None
        if temp is None:
            logger.warning("Temperature compensation skipped: no RTD reading")
            return None
        try:
            probe.set_temp_compensation(temp)
            return float(temp)
        except Exception as e:
            logger.warning(f"Failed to set temperature compensation: {e}")
            return None

    def _build_meta(self, mode, sensor, meta, params_used, temp_c):
        """Assemble the provenance block written at the top of the CSV."""
        info = {
            'mode': mode,
            'sensor': sensor,
            'date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'titrant': (meta or {}).get('titrant', ''),
            'analyte': (meta or {}).get('analyte', ''),
            'initial_volume_ml': (meta or {}).get('initial_volume_ml', ''),
            'notes': (meta or {}).get('notes', ''),
            'solution_temp_c': f"{temp_c:.2f}" if temp_c is not None else '',
            'pump_Q_mls': get_setting('PUMP_FLOW_RATE_MLS') or get_setting('DEFAULT_FLOW_RATE'),
            'pump_tau_s': get_setting('PUMP_DEAD_TIME_S') or 0.0,
            'pump_cal_date': get_setting('PUMP_CAL_DATE') or '',
            'ph_cal_date': get_setting('PH_CAL_DATE') or '',
            'ec_cal_date': get_setting('EC_CAL_DATE') or '',
        }
        info.update(params_used)
        return info

    def run_volumetric(self, experiment_name, volume_ml, project_name="Default",
                       params={}, meta=None):
        """
        Original Volumetric Titration (Add X mL, Read, Repeat).
        Typically used for Conductivity titration.
        """
        self.running = True
        self.stop_signal = False
        self.error = None
        self.last_result = None

        probe = self.hw.get_ec_probe()
        if not probe:
            self.running = False
            self._fail("Conductivity probe not available.")

        # Default Params from Config
        default_step = get_setting('VOLUME_PER_STEP', 0.5)
        default_wait = get_setting('TITRATION_WAIT_TIME', 2.0)
        default_count = get_setting('CONDUCTIVITY_READINGS', 10)

        flow_rate = params.get('flow_rate') # explicit override only
        step_volume = float(params.get('step_volume', default_step)) # mL
        wait_time = float(params.get('wait_time', default_wait)) # seconds
        readings_count = int(params.get('readings_count', default_count))

        # Calculate timing: t_on = V/Q + tau (per-actuation dead time)
        pump_time_per_step, q, tau = compute_pump_on_time(
            step_volume, float(flow_rate) if flow_rate else None)
        check_step_against_calibration(step_volume, tau, pump_time_per_step)
        total_steps = int(volume_ml / step_volume)

        self.current_data = {
            'Volume (mL)': [],
            'Reading': [],
            'StdDev': [],
            'Mode': 'Conductivity'
        }

        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        logger.info(f"Starting Volumetric Titration: {volume_ml}mL in {total_steps} steps.")

        try:
            # Temperature compensation + provenance
            temp_c = self._apply_temp_compensation(probe)
            run_meta = self._build_meta('volumetric', 'ec', meta, {
                'step_volume_ml': step_volume,
                'wait_time_s': wait_time,
                'readings_per_point': readings_count,
                'target_total_volume_ml': volume_ml,
            }, temp_c)

            # Baseline reading at v=0 before any dosing
            avg, std = self._read_stable(probe, count=readings_count)
            if avg is not None:
                self._update_data('Volume (mL)', 0.0)
                self._update_data('Reading', avg)
                self._update_data('StdDev', std)
                self._save_data(self.current_data, experiment_name, project_name,
                                timestamp, 'volumetric', run_meta)

            consecutive_failures = 0
            for i in range(total_steps):
                if self.stop_signal:
                    logger.info("Titration stopped by user.")
                    break

                # Dose
                self.hw.get_pump().start()
                time.sleep(pump_time_per_step)
                self.hw.get_pump().stop()

                # Mixing wait
                time.sleep(wait_time)

                # Reading
                avg, std = self._read_stable(probe, count=readings_count)
                if self.stop_signal and avg is None:
                    break  # stopped mid-read window, nothing to record

                current_vol = (i + 1) * step_volume

                if avg is None:
                    consecutive_failures += 1
                    logger.warning(
                        f"Step {i+1}: probe read failed "
                        f"({consecutive_failures} consecutive)")
                    if consecutive_failures >= 3:
                        self._fail("Probe unresponsive: 3 consecutive read failures.")
                    continue
                consecutive_failures = 0

                self._update_data('Volume (mL)', current_vol)
                self._update_data('Reading', avg)
                self._update_data('StdDev', std)

                logger.info(f"Step {i+1}/{total_steps}: {current_vol}mL -> {avg:.2f}")

                # Save Data Interval (Save every step to prevent data loss)
                self._save_data(self.current_data, experiment_name, project_name,
                                timestamp, 'volumetric', run_meta)

            self.last_result = {'mode': 'volumetric', 'timestamp': timestamp}
            return timestamp

        except Exception as e:
            if not self.error:
                self.error = str(e)
            logger.error(f"Titration error: {e}")
            raise
        finally:
            self.hw.get_pump().stop()
            self.running = False

    def run_endpoint(self, experiment_name, target_ph, project_name="Default",
                     params={}, meta=None):
        """
        Endpoint Titration (Add until pH reaches Target).

        Refinements over naive dosing:
        - baseline reading recorded at v=0
        - fine steps (step/divisor) once within ENDPOINT_FINE_WINDOW_PH of target
        - divergence guard aborts when pH keeps moving away from the target
        - endpoint volume linearly interpolated between the last two points
        """
        self.running = True
        self.stop_signal = False
        self.error = None
        self.last_result = None

        probe = self.hw.get_ph_probe()
        if not probe:
            self.running = False
            self._fail("pH probe not available.")

        # Default Params from Config
        default_step = get_setting('VOLUME_PER_STEP', 0.5)
        default_wait = get_setting('TITRATION_WAIT_TIME', 5.0)
        default_count = get_setting('PH_READINGS', 5)

        flow_rate = params.get('flow_rate') # explicit override only
        step_volume = float(params.get('step_volume', default_step)) # mL per dose
        wait_time = float(params.get('wait_time', default_wait))
        readings_count = int(params.get('readings_count', default_count))
        max_volume = float(params.get('max_volume',
                                      get_setting('ENDPOINT_MAX_VOLUME_ML', 100.0)))

        fine_window = float(get_setting('ENDPOINT_FINE_WINDOW_PH', 1.0))
        fine_divisor = max(1, int(get_setting('ENDPOINT_FINE_DIVISOR', 4)))
        divergence_limit = max(2, int(get_setting('ENDPOINT_DIVERGENCE_STEPS', 5)))

        _, q, tau = compute_pump_on_time(
            step_volume, float(flow_rate) if flow_rate else None)
        check_step_against_calibration(step_volume, tau, step_volume / q + tau)

        self.current_data = {
            'Volume (mL)': [],
            'pH': [],
            'StdDev': [],
            'Mode': 'pH'
        }

        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        current_vol = 0.0

        try:
            # Temperature compensation + provenance
            temp_c = self._apply_temp_compensation(probe)
            run_meta = self._build_meta('endpoint', 'ph', meta, {
                'step_volume_ml': step_volume,
                'wait_time_s': wait_time,
                'readings_per_point': readings_count,
                'target_ph': target_ph,
                'max_volume_ml': max_volume,
            }, temp_c)

            # Initial Read (recorded as the v=0 baseline)
            current_ph, std = self._read_stable(probe, count=readings_count)
            if current_ph is None:
                self._fail("Initial pH reading failed; check the probe.")

            self._update_data('Volume (mL)', 0.0)
            self._update_data('pH', current_ph)
            self._update_data('StdDev', std)
            self._save_data(self.current_data, experiment_name, project_name,
                            timestamp, 'endpoint', run_meta)

            logger.info(f"Starting pH: {current_ph:.2f}, Target: {target_ph}")

            # Direction is inferred from the start point; the divergence guard
            # below catches a titrant driving pH the wrong way.
            direction = 1 if target_ph > current_ph else -1

            wrong_way = 0
            consecutive_failures = 0
            prev_distance = abs(target_ph - current_ph)

            while (target_ph - current_ph) * direction > 0:
                if self.stop_signal:
                    break

                # Fine steps close to the target for endpoint resolution
                distance = abs(target_ph - current_ph)
                dose = step_volume / fine_divisor if distance <= fine_window else step_volume
                t_on = dose / q + tau

                # Dose
                self.hw.get_pump().start()
                time.sleep(t_on)
                self.hw.get_pump().stop()
                current_vol += dose

                # Wait
                time.sleep(wait_time)

                # Read
                new_ph, std = self._read_stable(probe, count=readings_count)
                if self.stop_signal and new_ph is None:
                    break  # stopped mid-read window

                if new_ph is None:
                    consecutive_failures += 1
                    logger.warning(f"pH read failed at {current_vol:.2f} mL "
                                   f"({consecutive_failures} consecutive)")
                    if consecutive_failures >= 3:
                        self._fail("Probe unresponsive: 3 consecutive read failures.")
                    continue
                consecutive_failures = 0
                current_ph = new_ph

                self._update_data('Volume (mL)', round(current_vol, 4))
                self._update_data('pH', current_ph)
                self._update_data('StdDev', std)

                logger.info(f"Vol: {current_vol:.2f}mL -> pH: {current_ph:.2f} "
                            f"(dose {dose:.3f} mL)")

                # Save Data Interval
                self._save_data(self.current_data, experiment_name, project_name,
                                timestamp, 'endpoint', run_meta)

                # Divergence guard: distance to target should shrink over time
                distance = abs(target_ph - current_ph)
                if distance > prev_distance + 0.01:
                    wrong_way += 1
                    if wrong_way >= divergence_limit:
                        self._fail(
                            f"pH moved away from target for {wrong_way} consecutive "
                            f"steps (now {current_ph:.2f}, target {target_ph}). "
                            f"Check titrant direction/concentration.")
                else:
                    wrong_way = 0
                prev_distance = distance

                if current_vol >= max_volume:
                    self._fail(f"Max volume reached ({max_volume:g} mL) "
                               f"without hitting target pH.")

            # Completed (target crossed) or stopped: summarize
            volumes = self.current_data.get('Volume (mL)', [])
            phs = self.current_data.get('pH', [])
            result = {'mode': 'endpoint', 'timestamp': timestamp,
                      'final_ph': current_ph, 'final_volume_ml': current_vol}

            crossed = (not self.stop_signal) and len(volumes) >= 2 \
                and (target_ph - current_ph) * direction <= 0
            if crossed:
                v_interp = interpolate_crossing(
                    volumes[-2], phs[-2], volumes[-1], phs[-1], target_ph)
                result['endpoint_volume_ml'] = round(v_interp, 4)
                run_meta['endpoint_volume_ml_interpolated'] = round(v_interp, 4)
                self._save_data(self.current_data, experiment_name, project_name,
                                timestamp, 'endpoint', run_meta)
                logger.info(f"Endpoint interpolated at {v_interp:.3f} mL "
                            f"(last dose crossed the target).")

            self.last_result = result
            return timestamp

        except Exception as e:
            if not self.error:
                self.error = str(e)
            logger.error(f"Endpoint Titration error: {e}")
            raise
        finally:
            self.hw.get_pump().stop()
            self.running = False


    def _read_stable(self, probe, count=10, delay=1.0):
        """Collect multiple readings and return mean/std. Returns None on error."""
        readings = []
        for _ in range(count):
            if self.stop_signal:
                break
            value = probe.read()
            if value is not None:  # Only include valid readings
                readings.append(value)
                # Cache latest reading for status endpoint
                if probe is self.hw.get_ec_probe():
                    self._last_ec = value
                elif probe is self.hw.get_ph_probe():
                    self._last_ph = value
            time.sleep(delay)

        if not readings:
            logger.warning("No valid readings collected")
            return None, None
        return float(np.mean(readings)), float(np.std(readings))



    def _save_data(self, data, experiment_name, project_name, timestamp, mode,
                   meta=None):
        """Save titration data to CSV in the project directory, with a
        commented provenance header (parseable via pandas comment='#')."""
        # Determine path relative to this file: src/titration/engine.py -> src/static/titrations
        current_dir = os.path.dirname(os.path.abspath(__file__))
        base_dir = os.path.join(os.path.dirname(current_dir), 'static', 'titrations')

        # Sanitize project name
        safe_project = "".join([c for c in project_name if c.isalnum() or c in (' ', '_', '-')]).strip()
        if not safe_project:
            safe_project = "Default"

        project_dir = os.path.join(base_dir, safe_project)
        os.makedirs(project_dir, exist_ok=True)

        filename = f"{experiment_name}_{mode}_{timestamp}.csv"
        filepath = os.path.join(project_dir, filename)

        try:
            header_lines = []
            if meta:
                header_lines.append("# Autotitrator run metadata")
                for k, v in meta.items():
                    v_str = str(v).replace('\n', ' ').replace('\r', ' ')
                    header_lines.append(f"# {k}: {v_str}")
            csv_body = pd.DataFrame(data).to_csv(index=False)
            with open(filepath, 'w') as f:
                if header_lines:
                    f.write('\n'.join(header_lines) + '\n')
                f.write(csv_body)
            logger.debug(f"Saved titration data to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save titration data: {e}")
