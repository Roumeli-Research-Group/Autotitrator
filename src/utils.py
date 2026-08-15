
import os
import json
import logging
import threading

logger = logging.getLogger(__name__)

# Config file path relative to this file's directory (src/)
_current_dir = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(os.path.dirname(_current_dir), 'config.json')

# Thread lock for config modifications
_config_lock = threading.Lock()

# Defaults
DEFAULT_SETTINGS = {
    'DEFAULT_FLOW_RATE': 0.2245,
    'VOLUME_PER_STEP': 0.5,
    'TITRATION_WAIT_TIME': 2,
    'MEASUREMENT_INITIAL_WAIT': 2,
    'MEASUREMENT_STEP_WAIT': 1,
    'CONDUCTIVITY_READINGS': 10,
    'PH_READINGS': 5,
    'CONDUCTIVITY_READ_DELAY': 0.1,
    'MEASUREMENT_POLL_INTERVAL': 1.0,
    'STABILITY_WINDOW': 5,
    'PH_STABILITY_THRESHOLD': 0.02,
    'EC_STABILITY_THRESHOLD_PCT': 1.0,
    'TEMP_STABILITY_THRESHOLD': 0.1,
    # Endpoint titration refinement
    'ENDPOINT_MAX_VOLUME_ML': 100.0,     # safety cap, overridable per run
    'ENDPOINT_FINE_WINDOW_PH': 1.0,      # within this of target -> fine steps
    'ENDPOINT_FINE_DIVISOR': 4,          # fine step = step / divisor
    'ENDPOINT_DIVERGENCE_STEPS': 5,      # abort after N consecutive wrong-way steps
    # Per-run titration settings memory (last 5 used)
    'TITRATION_RECENT_SETTINGS': [],
    # Per-device calibration dates
    'LAST_CALIBRATION_DATE': None,       # most recent of any (legacy/dashboard)
    'PH_CAL_DATE': None,
    'EC_CAL_DATE': None,
    'RTD_CAL_DATE': None,
    # Pump calibration (two-constant model: V = Q * (t_on - tau))
    'PUMP_FLOW_RATE_MLS': None,      # Q, mL/s (None -> fall back to DEFAULT_FLOW_RATE)
    'PUMP_DEAD_TIME_S': None,        # tau, s (None -> not fitted; treated as 0)
    'PUMP_CAL_STEP_VOLUME_ML': None, # step volume the calibration was done for
    'PUMP_CAL_ON_TIME_S': None,      # computed on-time for that step
    'PUMP_CAL_DATE': None,
    'PUMP_CAL_TEMP_C': None,
    'PUMP_CAL_GAP_S': None,
    'PUMP_CAL_R2': None,
    'PUMP_CAL_RESID_UL': None,
    'PUMP_CAL_RAW': None             # raw [[t_on, N, mass_g], ...] for re-fitting
}

def load_config():
    """Load configuration from JSON file."""
    with _config_lock:
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading config: {e}")
        return {}

def save_config(config_data):
    """Save configuration to JSON file."""
    with _config_lock:
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config_data, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving config: {e}")

# Module-level config, reloaded when config.json changes on disk so that
# external writers (pump_calibrate.py CLI, manual edits) are picked up by a
# running server without a restart.
config = load_config()
_config_mtime = os.path.getmtime(CONFIG_FILE) if os.path.exists(CONFIG_FILE) else 0

def _refresh_config():
    """Reload config from disk if the file changed since last load."""
    global config, _config_mtime
    try:
        mtime = os.path.getmtime(CONFIG_FILE)
    except OSError:
        return
    if mtime != _config_mtime:
        config = load_config()
        _config_mtime = mtime

def get_setting(key, default=None):
    """Get a setting value, falling back to defaults."""
    _refresh_config()
    if default is None:
        default = DEFAULT_SETTINGS.get(key)
    return config.get(key, default)

def update_setting(key, value):
    """Update a setting and persist to disk."""
    global config, _config_mtime
    _refresh_config()  # merge external changes before writing
    with _config_lock:
        config[key] = value
    save_config(config)
    try:
        _config_mtime = os.path.getmtime(CONFIG_FILE)
    except OSError:
        pass
