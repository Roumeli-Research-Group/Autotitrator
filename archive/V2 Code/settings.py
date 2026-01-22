# settings.py - Centralized System Defaults for Autotitrator
# These values can be overridden by config.json at runtime.

# --- Pump Configuration ---
DEFAULT_FLOW_RATE = 0.2245  # mL/s - Pump flow rate
VOLUME_PER_STEP = 0.5       # mL - Volume dispensed per titration step

# --- Timing Configuration (Production) ---
TITRATION_WAIT_TIME = 60    # seconds - Wait time after each dose for mixing/reaction
MEASUREMENT_INITIAL_WAIT = 20   # seconds - Initial wait before starting measurement phase
MEASUREMENT_STEP_WAIT = 120     # seconds - Wait time between measurements

# --- Conductivity Reading ---
CONDUCTIVITY_READINGS = 10  # Number of readings to average
CONDUCTIVITY_READ_DELAY = 1.0  # seconds - Delay between each reading

# --- Development Overrides ---
# When TITRATOR_ENV=DEV, these faster values are used for testing
DEV_TITRATION_WAIT_TIME = 2
DEV_MEASUREMENT_INITIAL_WAIT = 2
DEV_MEASUREMENT_STEP_WAIT = 2
DEV_CONDUCTIVITY_READ_DELAY = 0.1

# --- Limits ---
MAX_VOLUME_ML = 200  # Maximum allowed titration volume
MIN_VOLUME_ML = 0.5  # Minimum allowed titration volume
