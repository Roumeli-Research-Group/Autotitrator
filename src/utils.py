
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
    'LAST_CALIBRATION_DATE': None
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

# Module-level config (loaded once at import)
config = load_config()

def get_setting(key, default=None):
    """Get a setting value, falling back to defaults."""
    if default is None:
        default = DEFAULT_SETTINGS.get(key)
    return config.get(key, default)

def update_setting(key, value):
    """Update a setting and persist to disk."""
    global config
    with _config_lock:
        config[key] = value
    save_config(config)
