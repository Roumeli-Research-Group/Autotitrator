
import time
import copy
import threading
import logging
import numpy as np
from src.hardware import get_hardware
from src.utils import get_setting

logger = logging.getLogger(__name__)


class SingleMeasurement:
    """
    Timed single-probe measurement session.

    Reads the selected probe roughly once per second for up to `duration`
    seconds, recording (elapsed, value) pairs. If the last N readings are
    stable (std below threshold), the session ends early and reports the
    mean of that stable window as the result. Otherwise the mean of the
    final window is reported with stable=False.
    """

    def __init__(self):
        self.running = False
        self._stop_signal = False
        self._lock = threading.Lock()
        self._reset_state()

    def _reset_state(self):
        self._times = []
        self._values = []
        self._sensor = None
        self._duration = 0
        self._result = None       # float once finished
        self._stable = None       # True/False once finished
        self._stable_at = None    # elapsed seconds when plateau detected
        self._error = None
        self.last_value = None    # latest raw reading, for /api/status caching

    def start(self, sensor, duration):
        """Start a measurement thread. Raises RuntimeError if unavailable."""
        with self._lock:
            if self.running:
                raise RuntimeError("A measurement is already in progress")
            self.running = True
            self._stop_signal = False
            self._reset_state()
            self._sensor = sensor
            self._duration = duration

        hw = get_hardware()
        probe = hw.get_probe(sensor)
        if not probe:
            with self._lock:
                self.running = False
            raise RuntimeError(f"Probe '{sensor}' not available")

        thread = threading.Thread(target=self._run, args=(probe,), daemon=True)
        thread.start()

    def stop(self):
        self._stop_signal = True

    def _thresholds(self):
        """Return (window_size, std_threshold_fn) for the current sensor."""
        window = int(get_setting('STABILITY_WINDOW', 5))
        if self._sensor == 'ph':
            abs_thresh = float(get_setting('PH_STABILITY_THRESHOLD', 0.02))
            return window, lambda mean: abs_thresh
        elif self._sensor == 'temp':
            abs_thresh = float(get_setting('TEMP_STABILITY_THRESHOLD', 0.1))
            return window, lambda mean: abs_thresh
        else:
            pct = float(get_setting('EC_STABILITY_THRESHOLD_PCT', 1.0))
            # Relative threshold with a small absolute floor for near-zero EC
            return window, lambda mean: max(abs(mean) * pct / 100.0, 1.0)

    def _apply_temp_compensation(self, probe):
        """Push the RTD temperature to the measuring probe (EZO T,<t> command)."""
        if self._sensor == 'temp':
            return
        try:
            temp_probe = get_hardware().get_temp_probe()
            if temp_probe:
                temp = temp_probe.read()
                if temp is not None:
                    probe.set_temp_compensation(temp)
        except Exception as e:
            logger.warning(f"Temperature compensation skipped: {e}")

    def _run(self, probe):
        interval = float(get_setting('MEASUREMENT_POLL_INTERVAL', 1.0))
        window, thresh_fn = self._thresholds()
        self._apply_temp_compensation(probe)
        t0 = time.monotonic()

        try:
            while not self._stop_signal:
                elapsed = time.monotonic() - t0
                if elapsed >= self._duration:
                    break

                value = probe.read()
                elapsed = time.monotonic() - t0  # reading can block ~2s on real HW

                if value is not None:
                    with self._lock:
                        self._times.append(round(elapsed, 1))
                        self._values.append(float(value))
                        self.last_value = float(value)

                    # Plateau check on the last `window` readings
                    with self._lock:
                        tail = self._values[-window:]
                    if len(tail) >= window:
                        mean = float(np.mean(tail))
                        std = float(np.std(tail))
                        if std < thresh_fn(mean):
                            with self._lock:
                                self._result = mean
                                self._stable = True
                                self._stable_at = round(elapsed, 1)
                            logger.info(
                                f"Plateau detected at {elapsed:.1f}s: "
                                f"{mean:.3f} (std {std:.4f})")
                            return

                # Pace to ~interval between reads (read time already elapsed)
                remaining = interval - ((time.monotonic() - t0) % interval)
                if remaining > 0 and not self._stop_signal:
                    time.sleep(min(remaining, interval))

            # Timed out or stopped without plateau: report mean of final window
            with self._lock:
                tail = self._values[-window:]
                if tail:
                    self._result = float(np.mean(tail))
                    self._stable = False
                else:
                    self._error = "No valid readings collected"
            if self._error:
                logger.warning(f"Single measurement failed: {self._error}")
            else:
                logger.info(
                    f"Measurement ended without plateau. "
                    f"Mean of last {len(tail)} readings: {self._result:.3f}")

        except Exception as e:
            logger.error(f"Single measurement error: {e}")
            with self._lock:
                self._error = str(e)
        finally:
            self.running = False

    def get_data(self):
        """Thread-safe snapshot for the polling API."""
        with self._lock:
            return {
                'sensor': self._sensor,
                'duration': self._duration,
                'elapsed': copy.copy(self._times),
                'values': copy.copy(self._values),
                'running': self.running,
                'stable': self._stable,
                'stable_at': self._stable_at,
                'result': self._result,
                'error': self._error,
            }
