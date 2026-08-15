
from flask import Flask, render_template, request, jsonify, send_from_directory, abort
import os
import json
import logging
from logging.handlers import RotatingFileHandler
import datetime
import threading
import time
import atexit
from collections import deque
from src.hardware import get_hardware
from src.titration import TitrationEngine
from src.measurement import SingleMeasurement
from src.pump_control import PulseTrainRunner, fit_pulse_trials, water_density
from src.utils import get_setting, update_setting, DEFAULT_SETTINGS

# In-memory log buffer for API access (last 500 entries)
log_buffer = deque(maxlen=500)

class BufferHandler(logging.Handler):
    """Custom handler to store logs in memory buffer for API access."""
    def emit(self, record):
        log_entry = {
            'timestamp': datetime.datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S'),
            'level': record.levelname,
            'name': record.name,
            'message': self.format(record)
        }
        log_buffer.append(log_entry)

# Configure logging with file and buffer handlers
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'autotitrator.log')

# Create formatters and handlers
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# File handler (rotating, 5 MB max, keep 3 backups)
file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)

# Buffer handler for API
buffer_handler = BufferHandler()
buffer_handler.setFormatter(logging.Formatter('%(message)s'))
buffer_handler.setLevel(logging.INFO)

# Configure root logger
logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler, buffer_handler])
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Initialize Hardware & Engine
get_hardware()
engine = TitrationEngine()
single_measurement = SingleMeasurement()
pulse_runner = PulseTrainRunner()

# Single lock serializing all "start an activity that owns the pump/I2C bus"
# checks, so two simultaneous requests cannot both pass their guards.
_start_lock = threading.Lock()

def _busy_reason(ignore=None):
    """Name the activity currently owning the hardware, or None if idle."""
    if engine.running and ignore != 'titration':
        return 'Titration in progress'
    if single_measurement.running and ignore != 'measurement':
        return 'A single measurement is in progress'
    if pulse_runner.running and ignore != 'pulse':
        return 'Pump calibration pulse train is running'
    return None

@app.context_processor
def inject_env():
    """Every template can render the DEV/mock-data warning banner."""
    env = os.environ.get('TITRATOR_ENV', 'DEV')
    return {'titrator_env': env, 'is_mock': env != 'PROD'}

# Track active titration thread for clean shutdown
_active_thread = None
_shutdown_event = threading.Event()

def cleanup_on_exit():
    """Ensure titration stops cleanly on app shutdown."""
    global _active_thread
    if _active_thread and _active_thread.is_alive():
        logger.info("Shutting down: signaling titration to stop...")
        engine.stop()
        _active_thread.join(timeout=5.0)

atexit.register(cleanup_on_exit)

def _days_ago(date_str):
    """Human-readable age of a YYYY-MM-DD date string."""
    if not date_str:
        return "Never"
    try:
        last_date = datetime.datetime.strptime(date_str, '%Y-%m-%d')
        delta = datetime.datetime.now() - last_date
        if delta.days == 0:
            return "Today"
        elif delta.days == 1:
            return "Yesterday"
        return f"{delta.days} days ago"
    except Exception:
        return "Unknown"

@app.route('/')
def landing_page():
    # Per-device calibration ages
    cal_dates = {
        'pH': _days_ago(get_setting('PH_CAL_DATE')),
        'EC': _days_ago(get_setting('EC_CAL_DATE')),
        'RTD': _days_ago(get_setting('RTD_CAL_DATE')),
        'Pump': _days_ago(get_setting('PUMP_CAL_DATE')),
    }
    system_status = "Operational" if engine.running else "Idle"

    return render_template('index.html', cal_dates=cal_dates,
                           last_calibration=_days_ago(get_setting('LAST_CALIBRATION_DATE')),
                           system_status=system_status)

@app.route('/status')
def status_api():
    hw = get_hardware()
    pump = hw.get_pump()
    
    # Pump Status
    pump_running = False
    if pump:
        try:
            pump_running = pump.get_status()
        except:
            pass
            
    busy = engine.running or single_measurement.running
    return jsonify({
        'pump_status': pump_running,
        'measurement_status': busy,
        'system_status': "Operational" if busy else "Idle"
    })

# --- Probe value cache -------------------------------------------------------
# Each real Atlas read blocks the I2C bus for ~2s, so display polling (status
# bar, calibration pages) is throttled through this cache. While a titration
# or single measurement owns the bus, only cached values are served.
_probe_cache = {}
_probe_cache_lock = threading.Lock()
PROBE_CACHE_TTL = 3.0

def _get_probe_value(probe_type):
    """Last-known value for a probe. Reads hardware only when the system is
    idle and the cached value is older than PROBE_CACHE_TTL.
    Returns (value_or_None, cached: bool)."""
    now = time.monotonic()

    # Bus is owned by a running process: serve caches only
    if engine.running or single_measurement.running:
        if single_measurement.running:
            data = single_measurement.get_data()
            if data['sensor'] == probe_type and single_measurement.last_value is not None:
                return single_measurement.last_value, True
        if engine.running:
            if probe_type == 'ph':
                return engine._last_ph, True
            if probe_type == 'ec':
                return engine._last_ec, True
        cached = _probe_cache.get(probe_type)
        return (cached['value'] if cached else None), True

    with _probe_cache_lock:
        cached = _probe_cache.get(probe_type)
        if cached and now - cached['ts'] < PROBE_CACHE_TTL:
            return cached['value'], True

        probe = get_hardware().get_probe(probe_type)
        if not probe:
            return None, False
        try:
            val = probe.read()
        except Exception as e:
            logger.warning(f"Probe read failed for {probe_type}: {e}")
            val = None

        if val is not None:
            _probe_cache[probe_type] = {'value': float(val), 'ts': now}
            return float(val), False
        # Read failed: fall back to stale cache if we have one
        return (cached['value'] if cached else None), True

@app.route('/api/status')
def api_status():
    """API endpoint for status - compatible with frontend polling."""
    hw = get_hardware()
    pump = hw.get_pump()

    # Pump Status
    pump_running = False
    if pump:
        try:
            pump_running = pump.get_status()
        except:
            pass

    ec_val, _ = _get_probe_value('ec')
    ph_val, _ = _get_probe_value('ph')
    temp_val, _ = _get_probe_value('temp')

    return jsonify({
        'pump_status': pump_running,
        'pump': pump_running,  # Alias for frontend compatibility
        'measurement_status': engine.running,
        'running': engine.running,  # Alias for frontend compatibility
        'system_status': "Operational" if engine.running else "Idle",
        'ec': ec_val if ec_val is not None else 0.0,
        'ph': ph_val if ph_val is not None else 0.0,
        'temp': temp_val,  # None when unavailable (e.g. RTD not attached)
        'titration_error': engine.error,
        'env': os.environ.get('TITRATOR_ENV', 'DEV')
    })

@app.route('/start_pump', methods=['POST'])
def start_pump():
    """Start the pump manually."""
    try:
        busy = _busy_reason()
        if busy:
            return jsonify({'error': f'{busy} - manual pump control is locked'}), 400
        hw = get_hardware()
        pump = hw.get_pump()
        if pump:
            pump.start()
            return jsonify({'message': 'Pump started'})
        return jsonify({'error': 'Pump not initialized'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/stop_pump', methods=['POST'])
def stop_pump():
    """Stop the pump manually. Always allowed - this is the panic button -
    but it also signals any running activity to stop so they don't restart it."""
    try:
        if engine.running:
            engine.stop()
        if single_measurement.running:
            single_measurement.stop()
        if pulse_runner.running:
            pulse_runner.stop()
        hw = get_hardware()
        pump = hw.get_pump()
        if pump:
            pump.stop()
            return jsonify({'message': 'Pump stopped'})
        return jsonify({'error': 'Pump not initialized'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/measurement')
def measurement():
    return render_template('measurement.html')

@app.route('/settings')
def settings_page():
    return render_template('settings.html')

@app.route('/database')
def database():
    import re
    titrations_directory = os.path.join(app.root_path, 'static', 'titrations')
    os.makedirs(titrations_directory, exist_ok=True)
    
    # Structure: { 'ProjectName': [ {filestats...} ] }
    # But database.html expects a flat list "titrations". 
    # Let's keep providing a flat list for now, but enriched with project data.
    # The Frontend can then group them or we can send grouped data.
    # User asked for "Navigable as per projects". 
    # Let's send a list of projects separately or let the frontend extract uniqueness?
    # Better: Send a Dict of Projects and their Files to the template.
    
    projects_data = {}
    
    # 1. Get List of Projects (Subfolders)
    # Ensure Default exists
    default_path = os.path.join(titrations_directory, 'Default')
    os.makedirs(default_path, exist_ok=True)

    for item in os.listdir(titrations_directory):
        item_path = os.path.join(titrations_directory, item)
        if os.path.isdir(item_path):
            project_name = item
            projects_data[project_name] = []
            
            # Walk files in this project
            for f in os.listdir(item_path):
                if f.endswith('.csv'):
                    try:
                        # Parsing Logic
                        match = re.search(r'_(\d{8}_\d{6})\.csv$', f)
                        if match:
                            timestamp_str = match.group(1)
                            dt = datetime.datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                            date = dt.strftime('%Y-%m-%d')
                            time_val = dt.strftime('%H:%M:%S')
                            experiment_name = f[:f.rfind(f'_{timestamp_str}')]
                            
                            projects_data[project_name].append({
                                'filename': os.path.join(project_name, f), # relative to static/titrations/
                                'name': f,
                                'experiment_name': experiment_name,
                                'date': date,
                                'time': time_val,
                                'timestamp_obj': str(dt)
                            })
                        else:
                             projects_data[project_name].append({
                                'filename': os.path.join(project_name, f),
                                'name': f,
                                'experiment_name': f,
                                'date': 'Unknown',
                                'time': 'Unknown',
                                'timestamp_obj': ''
                            })
                    except Exception as e:
                        logger.warning(f"Error parsing file {f}: {e}")
                        continue
            
            # Sort files in project by date desc
            projects_data[project_name].sort(key=lambda x: x['timestamp_obj'], reverse=True)

    return render_template('database.html', projects=projects_data)


@app.route('/projects', methods=['GET', 'POST'])
def projects_api():
    base_dir = os.path.join(app.root_path, 'static', 'titrations')
    os.makedirs(base_dir, exist_ok=True)
    
    if request.method == 'GET':
        # List subdirectories
        projects = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
        if 'Default' not in projects:
            projects.append('Default')
            os.makedirs(os.path.join(base_dir, 'Default'), exist_ok=True)
        return jsonify(sorted(projects))
        
    if request.method == 'POST':
        data = request.json
        new_proj = data.get('name')
        if new_proj:
            # Sanitize
            safe_name = "".join([c for c in new_proj if c.isalnum() or c in (' ', '_', '-')]).strip()
            new_path = os.path.join(base_dir, safe_name)
            try:
                os.makedirs(new_path, exist_ok=True)
                return jsonify({'message': 'Project created'})
            except Exception as e:
                return jsonify({'error': str(e)}), 500
        return jsonify({'error': 'No name provided'}), 400

def _remember_titration_settings(entry):
    """Keep the last 5 distinct advanced-settings combinations."""
    recent = get_setting('TITRATION_RECENT_SETTINGS') or []
    recent = [r for r in recent if r != entry]  # dedup
    recent.insert(0, entry)
    update_setting('TITRATION_RECENT_SETTINGS', recent[:5])

@app.route('/api/titrate/start', methods=['POST'])
def start_titration_api():
    try:
        data = request.json
        mode = data.get('mode', 'volumetric') # 'volumetric' or 'endpoint'
        exp_name = data.get('experiment_name', 'Test')
        project = data.get('project_name', 'Default')

        # Advanced per-run params (fall back to config defaults in the engine)
        params = {'sensor': data.get('sensor', 'ec')}
        for key in ('step_volume', 'wait_time', 'readings_count', 'max_volume'):
            if data.get(key) not in (None, ''):
                params[key] = float(data[key])

        # Run provenance (written into the CSV header)
        meta = {
            'titrant': data.get('titrant', ''),
            'analyte': data.get('analyte', ''),
            'initial_volume_ml': data.get('initial_volume_ml', ''),
            'notes': data.get('notes', ''),
        }

        # Thread Target Wrapper
        def run_task():
            try:
                if mode == 'volumetric':
                    volume = float(data.get('volume_ml', 60))
                    engine.run_volumetric(exp_name, volume, project, params, meta)
                elif mode == 'endpoint':
                    target = float(data.get('target_ph', 7.0))
                    engine.run_endpoint(exp_name, target, project, params, meta)
            except Exception as e:
                logger.error(f"Threaded titration error: {e}")

        # Atomic check-and-start
        with _start_lock:
            busy = _busy_reason()
            if busy:
                return jsonify({'error': busy}), 400

            global _active_thread
            _active_thread = threading.Thread(target=run_task, daemon=False)
            _active_thread.start()

        # Remember the advanced settings used (only explicit overrides)
        overrides = {k: v for k, v in params.items() if k != 'sensor'}
        if overrides:
            overrides['mode'] = mode
            _remember_titration_settings(overrides)

        return jsonify({'message': 'Titration started'})

    except Exception as e:
        logger.error(f"Titration start failed: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/titrate/presets', methods=['GET'])
def get_titration_presets():
    """Last 5 advanced-settings combinations used, newest first."""
    return jsonify(get_setting('TITRATION_RECENT_SETTINGS') or [])

@app.route('/api/titrate/data', methods=['GET'])
def get_titration_data():
    if not engine.running and not engine.current_data:
        return jsonify({'error': engine.error} if engine.error else {})
    payload = engine.current_data
    payload['error'] = engine.error
    payload['result'] = engine.last_result
    return jsonify(payload)

@app.route('/api/titrate/stop', methods=['POST'])
def stop_titration_api():
    engine.stop()
    return jsonify({'message': 'Stop signal sent'})

@app.route('/api/projects/<name>', methods=['DELETE'])
def delete_project_api(name):
    base_dir = os.path.join(app.root_path, 'static', 'titrations')
    safe_name = "".join([c for c in name if c.isalnum() or c in (' ', '_', '-')]).strip()
    target_path = os.path.join(base_dir, safe_name)
    
    if not os.path.exists(target_path):
        return jsonify({'error': 'Project not found'}), 404
        
    if safe_name == 'Default':
         return jsonify({'error': 'Cannot delete Default project'}), 400
         
    try:
        # Only delete if empty? Or recursive? 
        # User said "option of creating and deleting". Usually implies recursive or check.
        # Let's do recursive for convenience but careful.
        import shutil
        shutil.rmtree(target_path)
        return jsonify({'message': 'Project deleted'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def _safe_titration_path(filename):
    """Resolve filename inside static/titrations; None if it escapes the tree.
    Uses commonpath (segment-aware), not commonprefix (character-based)."""
    base_dir = os.path.abspath(os.path.join(app.root_path, 'static', 'titrations'))
    full_path = os.path.abspath(os.path.join(base_dir, filename))
    try:
        if os.path.commonpath([full_path, base_dir]) != base_dir:
            return None
    except ValueError:  # different drives (Windows) or mixed abs/rel
        return None
    return full_path

@app.route('/api/titrations/<path:filename>', methods=['DELETE'])
def delete_file_api(filename):
    # filename is like "ProjectName/file.csv"
    base_dir = os.path.join(app.root_path, 'static', 'titrations')
    full_path = _safe_titration_path(filename)
    if full_path is None:
        return jsonify({'error': 'Invalid path'}), 400

    if not os.path.exists(full_path):
        return jsonify({'error': 'File not found'}), 404

    try:
        os.remove(full_path)
        return jsonify({'message': 'File deleted'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings', methods=['GET'])
def get_settings_api():
    data = {}
    for k in DEFAULT_SETTINGS.keys():
        data[k] = get_setting(k)
    return jsonify(data)

@app.route('/api/settings', methods=['POST'])
def update_settings_api():
    try:
        data = request.json
        unknown = [k for k in data if k not in DEFAULT_SETTINGS]
        if unknown:
            return jsonify({'error': f'Unknown settings: {", ".join(unknown)}'}), 400
        for k, v in data.items():
            update_setting(k, v)
        return jsonify({'message': 'Settings saved'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings/reset', methods=['POST'])
def reset_settings_api():
    """Restore every setting to its default (including clearing calibrations)."""
    try:
        for k, v in DEFAULT_SETTINGS.items():
            update_setting(k, v)
        logger.warning("All settings reset to defaults")
        return jsonify({'message': 'Defaults restored'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/calibration')
def calibration_page():
    return render_template('calibration.html')

@app.route('/pump_calibration')
def pump_calibration_page():
    return render_template('pump_calibration.html')

@app.route('/get_conductivity_data', methods=['GET'])
def get_conductivity_data():
    """Legacy live EC endpoint - served through the probe cache so it cannot
    collide with a running titration/measurement on the I2C bus."""
    val, _ = _get_probe_value('ec')
    return jsonify({'conductivity': val if val is not None else 0.0})

# Per-device calibration date keys
_CAL_DATE_KEYS = {'ph': 'PH_CAL_DATE', 'ec': 'EC_CAL_DATE', 'temp': 'RTD_CAL_DATE'}

def _run_probe_calibration(probe_type, cal_type, value):
    """Shared calibration logic for all Atlas probes (Cal,* commands)."""
    busy = _busy_reason()
    if busy:
        return jsonify({'error': f'Cannot calibrate: {busy}'}), 400

    hw = get_hardware()
    probe = hw.get_probe(probe_type)

    if not probe:
        return jsonify({'error': f'{probe_type.upper()} probe not initialized'}), 500

    response = probe.calibrate(cal_type, value)

    # Per-device calibration date (clear does not count as a calibration)
    if cal_type != 'clear':
        today = datetime.datetime.now().strftime('%Y-%m-%d')
        update_setting(_CAL_DATE_KEYS[probe_type], today)
        update_setting('LAST_CALIBRATION_DATE', today)

    return jsonify({'message': f'Calibration command sent: {response}'})

@app.route('/start_conductivity_calibration', methods=['POST'])
def start_conductivity_calibration():
    try:
        data = request.json
        return _run_probe_calibration('ec', data.get('type'), data.get('value'))
    except Exception as e:
        logger.error(f"Calibration error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/start_ph_calibration', methods=['POST'])
def start_ph_calibration():
    """3-point pH calibration via Atlas EZO firmware (Cal,mid / Cal,low / Cal,high).
    Note: Cal,mid clears any existing calibration, so it must be performed first."""
    try:
        data = request.json
        cal_type = data.get('type')
        value = data.get('value')

        if cal_type not in ('mid', 'low', 'high', 'clear'):
            return jsonify({'error': f'Invalid pH calibration type: {cal_type}'}), 400

        return _run_probe_calibration('ph', cal_type, value)
    except Exception as e:
        logger.error(f"pH calibration error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/start_rtd_calibration', methods=['POST'])
def start_rtd_calibration():
    """Single-point RTD calibration (Cal,<known temp>) or Cal,clear."""
    try:
        data = request.json
        cal_type = data.get('type')
        value = data.get('value')

        if cal_type not in ('point', 'clear'):
            return jsonify({'error': f'Invalid RTD calibration type: {cal_type}'}), 400
        if cal_type == 'point':
            try:
                value = float(value)
            except (TypeError, ValueError):
                return jsonify({'error': 'A known temperature value is required'}), 400
            if not (-10 <= value <= 120):
                return jsonify({'error': 'Calibration temperature must be -10 to 120 C'}), 400

        return _run_probe_calibration('temp', cal_type, value)
    except Exception as e:
        logger.error(f"RTD calibration error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/probe_health/<probe_type>', methods=['GET'])
def probe_health_api(probe_type):
    """Calibration state / electrode health from the EZO firmware.
    pH adds Slope,? (electrode aging); EC adds K,? (cell constant)."""
    if probe_type not in ('ph', 'ec', 'temp'):
        return jsonify({'error': 'Unknown probe type'}), 400

    busy = _busy_reason()
    if busy:
        return jsonify({'error': f'Probe busy: {busy}'}), 409

    probe = get_hardware().get_probe(probe_type)
    if not probe:
        return jsonify({'error': f'{probe_type.upper()} probe not initialized'}), 500
    try:
        health = probe.get_health()
        health['cal_date'] = get_setting(_CAL_DATE_KEYS[probe_type])
        return jsonify(health)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/probe_read/<probe_type>', methods=['GET'])
def probe_read_api(probe_type):
    """Live reading of a single probe (used by calibration pages and status bar)."""
    if probe_type not in ('ph', 'ec', 'temp'):
        return jsonify({'error': 'Unknown probe type'}), 400

    value, cached = _get_probe_value(probe_type)
    if value is None:
        return jsonify({'error': f'{probe_type.upper()} probe not available'}), 500
    return jsonify({'value': value, 'cached': cached})

@app.route('/api/measure/start', methods=['POST'])
def start_single_measurement():
    """Start a timed single measurement with live polling and plateau detection."""
    try:
        data = request.json or {}
        sensor = data.get('sensor', 'ec')
        duration = float(data.get('duration', 30))

        if sensor not in ('ph', 'ec', 'temp'):
            return jsonify({'error': 'Invalid sensor'}), 400
        if not (5 <= duration <= 600):
            return jsonify({'error': 'Duration must be between 5 and 600 seconds'}), 400

        with _start_lock:
            busy = _busy_reason(ignore='measurement')
            if busy:
                return jsonify({'error': busy}), 400
            single_measurement.start(sensor, duration)
        return jsonify({'message': 'Measurement started'})
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Measurement start failed: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/measure/data', methods=['GET'])
def get_single_measurement_data():
    return jsonify(single_measurement.get_data())

@app.route('/api/measure/stop', methods=['POST'])
def stop_single_measurement():
    single_measurement.stop()
    return jsonify({'message': 'Stop signal sent'})

# --- Pump calibration (pulse-train gravimetric) ------------------------------

@app.route('/api/pump/pulse', methods=['POST'])
def start_pulse_train():
    """Fire N relay pulses at an explicit on-time. Used by pump calibration:
    drives the relay directly, bypassing the volume-to-time conversion that
    is being calibrated."""
    try:
        data = request.json or {}
        on_time = float(data.get('on_time', 0))
        pulses = int(data.get('pulses', 1))
        gap = float(data.get('gap', 0.3))

        if not (0.02 <= on_time <= 60):
            return jsonify({'error': 'on_time must be between 0.02 and 60 s'}), 400
        if not (1 <= pulses <= 500):
            return jsonify({'error': 'pulses must be between 1 and 500'}), 400
        if not (0 <= gap <= 10):
            return jsonify({'error': 'gap must be between 0 and 10 s'}), 400

        with _start_lock:
            busy = _busy_reason(ignore='pulse')
            if busy:
                return jsonify({'error': busy}), 400
            pulse_runner.start(on_time, pulses, gap)
        return jsonify({'message': f'Firing {pulses} pulses of {on_time}s'})
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Pulse train start failed: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/pump/pulse_status', methods=['GET'])
def pulse_train_status():
    return jsonify(pulse_runner.get_status())

@app.route('/api/pump/pulse_stop', methods=['POST'])
def stop_pulse_train():
    pulse_runner.stop()
    return jsonify({'message': 'Stop signal sent'})

@app.route('/api/pump/calibration/fit', methods=['POST'])
def fit_pump_calibration():
    """Least-squares fit of the duration series: v_pulse = Q*t - Q*tau."""
    try:
        data = request.json or {}
        trials = data.get('trials', [])
        temp_c = float(data.get('temp_c', 25.0))
        result = fit_pulse_trials(trials, temp_c)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Pump calibration fit failed: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/pump/calibration', methods=['GET'])
def get_pump_calibration():
    """Current stored pump calibration."""
    keys = ['PUMP_FLOW_RATE_MLS', 'PUMP_DEAD_TIME_S', 'PUMP_CAL_STEP_VOLUME_ML',
            'PUMP_CAL_ON_TIME_S', 'PUMP_CAL_DATE', 'PUMP_CAL_TEMP_C',
            'PUMP_CAL_GAP_S', 'PUMP_CAL_R2', 'PUMP_CAL_RESID_UL']
    cal = {k: get_setting(k) for k in keys}
    cal['DEFAULT_FLOW_RATE'] = get_setting('DEFAULT_FLOW_RATE')
    cal['VOLUME_PER_STEP'] = get_setting('VOLUME_PER_STEP')
    return jsonify(cal)

@app.route('/api/pump/calibration/save', methods=['POST'])
def save_pump_calibration():
    """Persist a completed calibration (full fit or quick re-cal)."""
    try:
        data = request.json or {}
        q = float(data.get('Q'))
        tau = data.get('tau')  # may be None (quick re-cal keeps prior tau)
        step_volume = float(data.get('step_volume'))

        if q <= 0:
            return jsonify({'error': 'Invalid flow rate'}), 400

        tau_val = float(tau) if tau is not None else (get_setting('PUMP_DEAD_TIME_S') or 0.0)
        on_time = step_volume / q + tau_val

        update_setting('PUMP_FLOW_RATE_MLS', q)
        if tau is not None:
            update_setting('PUMP_DEAD_TIME_S', float(tau))
        update_setting('PUMP_CAL_STEP_VOLUME_ML', step_volume)
        update_setting('PUMP_CAL_ON_TIME_S', round(on_time, 4))
        update_setting('PUMP_CAL_DATE', datetime.datetime.now().strftime('%Y-%m-%d'))
        for key, field in [('PUMP_CAL_TEMP_C', 'temp_c'), ('PUMP_CAL_GAP_S', 'gap'),
                           ('PUMP_CAL_R2', 'r2'), ('PUMP_CAL_RESID_UL', 'resid_rms_ul')]:
            if data.get(field) is not None:
                update_setting(key, data[field])
        if data.get('raw') is not None:
            update_setting('PUMP_CAL_RAW', data['raw'])

        # Keep legacy consumers (settings page, old code paths) coherent
        update_setting('DEFAULT_FLOW_RATE', q)
        update_setting('LAST_CALIBRATION_DATE', datetime.datetime.now().strftime('%Y-%m-%d'))

        logger.info(f"Pump calibration saved: Q={q:.4f} mL/s, tau={tau_val*1000:.1f} ms, "
                    f"step={step_volume} mL, on_time={on_time:.4f} s")
        return jsonify({'message': 'Calibration saved', 'on_time': round(on_time, 4)})
    except (TypeError, ValueError) as e:
        return jsonify({'error': f'Invalid payload: {e}'}), 400
    except Exception as e:
        logger.error(f"Pump calibration save failed: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/probe_temp_density', methods=['GET'])
def probe_temp_density():
    """Temperature from the RTD probe plus the matching water density."""
    value, cached = _get_probe_value('temp')
    if value is None:
        return jsonify({'temp_c': None, 'rho': None, 'cached': cached})
    return jsonify({'temp_c': value, 'rho': water_density(value), 'cached': cached})

@app.route('/start_pump_duration', methods=['POST'])
def start_pump_duration():
    try:
        data = request.json
        duration = float(data.get('duration', 0))
        if duration <= 0:
            return jsonify({'error': 'Invalid duration'}), 400
        if pulse_runner.running:
            return jsonify({'error': 'Pump calibration pulse train is running'}), 400
            
        hw = get_hardware()
        pump = hw.get_pump()
        
        def run_pump():
            pump.start()
            time.sleep(duration)
            pump.stop()
            
        thread = threading.Thread(target=run_pump)
        thread.daemon = True
        thread.start()
        
        return jsonify({'message': f'Pump running for {duration}s'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download/<path:filename>')
def download_file(filename):
    # Path traversal protection (segment-aware)
    if _safe_titration_path(filename) is None:
        abort(400, description='Invalid path')

    return send_from_directory('static/titrations', filename)

@app.route('/api/titrations/data/<path:filename>', methods=['GET'])
def titration_csv_data(filename):
    """Parse a stored titration CSV (provenance header + table) as JSON,
    for in-browser plotting and comparison."""
    full_path = _safe_titration_path(filename)
    if full_path is None:
        return jsonify({'error': 'Invalid path'}), 400
    if not os.path.isfile(full_path) or not full_path.endswith('.csv'):
        return jsonify({'error': 'File not found'}), 404

    try:
        meta = {}
        with open(full_path) as f:
            for line in f:
                if not line.startswith('#'):
                    break
                stripped = line.lstrip('#').strip()
                if ':' in stripped:
                    k, _, v = stripped.partition(':')
                    meta[k.strip()] = v.strip()

        import pandas as pd
        df = pd.read_csv(full_path, comment='#')
        return jsonify({
            'filename': filename,
            'meta': meta,
            'columns': list(df.columns),
            'data': {col: df[col].tolist() for col in df.columns},
        })
    except Exception as e:
        logger.error(f"Failed to parse {filename}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/titrations/export/<project>', methods=['GET'])
def export_project_zip(project):
    """Download every CSV in a project as a single zip."""
    import io
    import zipfile
    from flask import send_file

    project_path = _safe_titration_path(project)
    if project_path is None or not os.path.isdir(project_path):
        return jsonify({'error': 'Project not found'}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(os.listdir(project_path)):
            if f.endswith('.csv'):
                zf.write(os.path.join(project_path, f), arcname=f)
    buf.seek(0)
    safe_name = "".join(c for c in project if c.isalnum() or c in (' ', '_', '-')).strip() or 'project'
    return send_file(buf, mimetype='application/zip', as_attachment=True,
                     download_name=f'{safe_name}_titrations.zip')

@app.route('/logs')
def logs_page():
    """Render the logs viewer page."""
    return render_template('logs.html')

@app.route('/api/logs', methods=['GET'])
def get_logs_api():
    """Get recent log entries from memory buffer."""
    level_filter = request.args.get('level', None)
    limit = min(int(request.args.get('limit', 100)), 500)
    
    logs = list(log_buffer)
    
    # Filter by level if specified
    if level_filter and level_filter.upper() in ['INFO', 'WARNING', 'ERROR', 'DEBUG']:
        logs = [l for l in logs if l['level'] == level_filter.upper()]
    
    # Return most recent entries (reversed so newest first)
    return jsonify(logs[-limit:][::-1])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
