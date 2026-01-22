
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

class TitrationEngine:
    def __init__(self):
        self.running = False
        self.stop_signal = False
        self.hw = get_hardware()
        self._data_lock = threading.Lock()  # Thread safety for shared state
        self._current_data = {}  # Shared state for graph

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

    def run_volumetric(self, experiment_name, volume_ml, project_name="Default", params={}):
        """
        Original Volumetric Titration (Add X mL, Read, Repeat).
        Typically used for Conductivity titration.
        """
        self.running = True
        self.stop_signal = False
        
        probe = self.hw.get_ec_probe()
        if not probe:
            raise RuntimeError("Conductivity probe not available.")


        # Default Params from Config
        default_flow = get_setting('DEFAULT_FLOW_RATE', 0.2245)
        default_step = get_setting('VOLUME_PER_STEP', 0.5)
        default_wait = get_setting('TITRATION_WAIT_TIME', 2.0)
        default_count = get_setting('CONDUCTIVITY_READINGS', 10)

        flow_rate = float(params.get('flow_rate', default_flow)) # mL/s
        step_volume = float(params.get('step_volume', default_step)) # mL
        wait_time = float(params.get('wait_time', default_wait)) # seconds
        readings_count = int(params.get('readings_count', default_count))
        
        # Calculate timing
        pump_time_per_step = step_volume / flow_rate if flow_rate > 0 else 0
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
                
                current_vol = (i + 1) * step_volume
                self._update_data('Volume (mL)', current_vol)
                self._update_data('Reading', avg)
                self._update_data('StdDev', std)
                
                
                logger.info(f"Step {i+1}/{total_steps}: {current_vol}mL -> {avg:.2f}")

                # Save Data Interval (Save every step to prevent data loss)
                self._save_data(self.current_data, experiment_name, project_name, timestamp, 'volumetric')

            return timestamp

        except Exception as e:
            logger.error(f"Titration error: {e}")
            raise
        finally:
            self.hw.get_pump().stop()
            self.running = False

    def run_endpoint(self, experiment_name, target_ph, project_name="Default", params={}):
        """
        Endpoint Titration (Add until pH reaches Target).
        """
        self.running = True
        self.stop_signal = False

        probe = self.hw.get_ph_probe()
        if not probe:
             raise RuntimeError("pH probe not available.")

        # Default Params from Config
        default_flow = get_setting('DEFAULT_FLOW_RATE', 0.2245)
        default_step = get_setting('VOLUME_PER_STEP', 0.5)
        default_wait = get_setting('TITRATION_WAIT_TIME', 5.0) # pH might need longer?
        default_count = get_setting('CONDUCTIVITY_READINGS', 5) # Reusing setting or needs new one?

        flow_rate = float(params.get('flow_rate', default_flow)) 
        step_volume = float(params.get('step_volume', default_step)) # mL per dose
        wait_time = float(params.get('wait_time', default_wait)) 
        readings_count = int(params.get('readings_count', default_count))
        
        pump_time_per_step = step_volume / flow_rate if flow_rate > 0 else 0
        
        self.current_data = {
            'Volume (mL)': [],
            'pH': [],
            'StdDev': [],
            'Mode': 'pH'
        }

        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        current_vol = 0.0
        
        # Initial Read
        current_ph, _ = self._read_stable(probe, count=readings_count)
        logger.info(f"Starting pH: {current_ph:.2f}, Target: {target_ph}")
        
        # Determine direction
        direction = 1 if target_ph > current_ph else -1 # 1 = increase pH (add base?), -1 = decrease (add acid?)
        # Actually we don't know the titrant type, so we just run until we cross the target.
        # Simple checkpoint: if we are supposed to go UP but pH goes DOWN, we might warn?
        # For now, simplistic "Run until crossed".
        
        # Condition: loop while (target > current) if going up, OR (target < current) if going down
        # Unified: (target - current) * direction > 0
        
        try:
            while (target_ph - current_ph) * direction > 0:
                if self.stop_signal:
                    break
                
                # Dose
                self.hw.get_pump().start()
                time.sleep(pump_time_per_step)
                self.hw.get_pump().stop()
                current_vol += step_volume
                
                # Wait
                time.sleep(wait_time)
                
                # Read
                current_ph, std = self._read_stable(probe, count=readings_count)
                
                self._update_data('Volume (mL)', current_vol)
                self._update_data('pH', current_ph)
                self._update_data('StdDev', std)
                
                logger.info(f"Vol: {current_vol}mL -> pH: {current_ph:.2f}")
                
                # Save Data Interval
                self._save_data(self.current_data, experiment_name, project_name, timestamp, 'endpoint')
                
                if current_vol > 100: # Safety Cap
                    logger.warning("Max volume reached (100mL). Stopping.")
                    break

            return timestamp

        except Exception as e:
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
            time.sleep(delay)
        
        if not readings:
            logger.warning("No valid readings collected")
            return None, None
        return float(np.mean(readings)), float(np.std(readings))



    def _save_data(self, data, experiment_name, project_name, timestamp, mode):
        """Save titration data to CSV file in the project directory."""
        # Determine path relative to this file: src/titration/engine.py -> src/static/titrations
        current_dir = os.path.dirname(os.path.abspath(__file__))
        base_dir = os.path.join(os.path.dirname(current_dir), 'static', 'titrations')
        
        # Sanitize project name
        safe_project = "".join([c for c in project_name if c.isalnum() or c in (' ', '_', '-')]).strip()
        if not safe_project:
            safe_project = "Default"
        
        # Use the computed base_dir (FIX: was using hardcoded 'src' before)
        project_dir = os.path.join(base_dir, safe_project)
        os.makedirs(project_dir, exist_ok=True)
        
        filename = f"{experiment_name}_{mode}_{timestamp}.csv"
        filepath = os.path.join(project_dir, filename)
        
        try:
            pd.DataFrame(data).to_csv(filepath, index=False)
            logger.info(f"Saved titration data to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save titration data: {e}")

