import os
import datetime
import time
import json
import logging
import pandas as pd
import numpy as np
from hardware_manager import get_hardware
import settings

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration File Path
CONFIG_FILE = 'config.json'

def load_config():
    """Load configuration from JSON file."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading config: {e}")
    return {}

def save_config(config):
    """Save configuration to JSON file."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving config: {e}")

# Load initial flow rate from config or use system default
# Load initial flow rate from config or use system default
config = load_config()

def get_setting(key, default):
    """Get setting from config, falling back to default."""
    return config.get(key, default)

def update_setting(key, value):
    """Update a specific setting and save config."""
    global config
    config[key] = value
    save_config(config)
    # If special handling needed for certain keys (like FLOW_RATE affecting calculated globals)
    if key == 'FLOW_RATE':
        global FLOW_RATE, PUMP_TIME_PER_ML
        FLOW_RATE = float(value)
        PUMP_TIME_PER_ML = settings.VOLUME_PER_STEP / FLOW_RATE if FLOW_RATE > 0 else 0

# Initialize globals dependent on settings
FLOW_RATE = float(get_setting('FLOW_RATE', settings.DEFAULT_FLOW_RATE))
PUMP_TIME_PER_ML = settings.VOLUME_PER_STEP / FLOW_RATE if FLOW_RATE > 0 else 0


def set_flow_rate(new_rate):
    """Update the global flow rate and save it."""
    update_setting('FLOW_RATE', float(new_rate))
    logger.info(f"Flow Rate updated to {FLOW_RATE} mL/s")

# Hardware Control Wrappers
def start_pump():
    get_hardware().get_pump().start()

def stop_pump():
    get_hardware().get_pump().stop()

def get_pump_status():
    return get_hardware().get_pump().get_status()

def sanitize_project_name(name):
    """Sanitize project name to be filesystem safe."""
    safe = "".join([c for c in name if c.isalnum() or c in (' ', '_', '-')]).strip()
    return safe if safe else "Default"

def save_titration_data(filename, data):
    """Save titration data to a file."""
    file_path = f'static/titrations/{filename}'
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    with open(file_path, 'w') as f:
        for line in data:
            f.write(','.join(map(str, line)) + '\n')
            
def start_titration(experiment_name, volume_ml, timestamp, project_name="Default"):
    """Start titration process and save real-time conductivity data using 10-second averages."""
    total_duration = volume_ml * PUMP_TIME_PER_ML
    
    # Get timing values based on environment
    is_dev = os.environ.get('TITRATOR_ENV') == 'DEV'
    
    # Dynamic Settings
    titration_wait = get_setting('TITRATION_WAIT_TIME', settings.TITRATION_WAIT_TIME)
    dev_titration_wait = get_setting('DEV_TITRATION_WAIT_TIME', settings.DEV_TITRATION_WAIT_TIME)
    
    wait_time = dev_titration_wait if is_dev else titration_wait
    volume_per_step = get_setting('VOLUME_PER_STEP', settings.VOLUME_PER_STEP)
    
    # Initialize data structure
    data = {
        'Volume (mL)': [],
        'Average Conductivity (µS/cm)': [],
        'Standard Deviation (µS/cm)': []
    }

    try:
        steps = int(volume_ml / volume_per_step)
        
        for i in range(steps):
            # Pump ON - dose one step
            get_hardware().get_pump().start()
            time.sleep(PUMP_TIME_PER_ML)
            
            # Pump OFF
            get_hardware().get_pump().stop()
            
            # Wait for mixing/reaction
            time.sleep(wait_time) 

            # Read conductivity
            avg_conductivity, std_dev_conductivity = read_conductivity_for_10_seconds()

            current_volume = (i + 1) * volume_per_step
            data['Volume (mL)'].append(current_volume)
            data['Average Conductivity (µS/cm)'].append(avg_conductivity)
            data['Standard Deviation (µS/cm)'].append(std_dev_conductivity)

            logger.info(f"Volume: {current_volume} mL, Avg Cond: {avg_conductivity}, Std Dev: {std_dev_conductivity}")

            time.sleep(1)

        # Save data
        df = pd.DataFrame(data)
        filename = f'{experiment_name}_{timestamp}.csv'
        
        safe_project = sanitize_project_name(project_name)
        filepath = os.path.join('static', 'titrations', safe_project, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        df.to_csv(filepath, index=False)
        logger.info(f"Titration data saved to {filepath}")

    except Exception as e:
        logger.error(f'Error during titration: {str(e)}')
    finally:
        stop_pump()

    return filename, datetime.datetime.now().strftime('%Y-%m-%d'), datetime.datetime.now().strftime('%H:%M:%S'), total_duration


def read_conductivity_for_10_seconds():
    """Read conductivity multiple times and return average and std dev."""
    values = []
    
    is_dev = os.environ.get('TITRATOR_ENV') == 'DEV'
    
    read_delay = get_setting('CONDUCTIVITY_READ_DELAY', settings.CONDUCTIVITY_READ_DELAY)
    dev_read_delay = get_setting('DEV_CONDUCTIVITY_READ_DELAY', settings.DEV_CONDUCTIVITY_READ_DELAY)
    delay = dev_read_delay if is_dev else read_delay
    
    readings_count = int(get_setting('CONDUCTIVITY_READINGS', settings.CONDUCTIVITY_READINGS))

    for _ in range(readings_count):
        try:
            val = get_hardware().get_probe().read_conductivity()
            values.append(val)
        except Exception as e:
            logger.error(f"Error reading conductivity: {e}")
        
        time.sleep(delay)

    if values:
        avg_value = np.mean(values)
        std_dev = np.std(values)
    else:
        avg_value = 0.0
        std_dev = 0.0

    return avg_value, std_dev


def start_measurement(experiment_name, timestamp, project_name="Default"):
    """Perform conductivity measurement after titration."""
    data = {
        'Measurement': [],
        'Average Conductivity': [],
        'Standard Deviation': []
    }

    logger.info("Waiting for titration to complete...")
    
    is_dev = os.environ.get('TITRATOR_ENV') == 'DEV'
    
    initial_wait = get_setting('MEASUREMENT_INITIAL_WAIT', settings.MEASUREMENT_INITIAL_WAIT)
    step_wait = get_setting('MEASUREMENT_STEP_WAIT', settings.MEASUREMENT_STEP_WAIT)
    dev_initial_wait = get_setting('DEV_MEASUREMENT_INITIAL_WAIT', settings.DEV_MEASUREMENT_INITIAL_WAIT)
    dev_step_wait = get_setting('DEV_MEASUREMENT_STEP_WAIT', settings.DEV_MEASUREMENT_STEP_WAIT)
    
    wait_initial = dev_initial_wait if is_dev else initial_wait
    wait_step = dev_step_wait if is_dev else step_wait

    time.sleep(wait_initial)

    for measurement_num in range(1, 4):
        logger.info(f"Starting measurement {measurement_num}...")
        time.sleep(wait_step)

        avg_value, std_dev = read_conductivity_for_10_seconds()

        logger.info(f"Measurement {measurement_num}: Avg={avg_value}, Std={std_dev}")

        data['Measurement'].append(measurement_num)
        data['Average Conductivity'].append(avg_value)
        data['Standard Deviation'].append(std_dev)

    df = pd.DataFrame(data)
    filename = f'{experiment_name}_measurement_{timestamp}.csv'
    
    safe_project = sanitize_project_name(project_name)
    filepath = os.path.join('static', 'measurements', safe_project, filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    df.to_csv(filepath, index=False)
    logger.info(f"Measurement data saved to {filepath}")

    return filename
