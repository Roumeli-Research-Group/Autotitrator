
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

@app.route('/')
def landing_page():
    last_cal = get_setting('LAST_CALIBRATION_DATE')
    days_ago = "Never"
    
    if last_cal:
        try:
            # Format: YYYY-MM-DD
            last_date = datetime.datetime.strptime(last_cal, '%Y-%m-%d')
            delta = datetime.datetime.now() - last_date
            if delta.days == 0:
                days_ago = "Today"
            elif delta.days == 1:
                days_ago = "Yesterday"
            else:
                days_ago = f"{delta.days} days ago"
        except:
            days_ago = "Unknown"
            
    system_status = "Operational" if engine.running else "Idle"
    
    return render_template('index.html', last_calibration=days_ago, system_status=system_status)

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
            
    return jsonify({
        'pump_status': pump_running,
        'measurement_status': engine.running,
        'system_status': "Operational" if engine.running else "Idle"
    })

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
    
    # Probe readings: use cached values during titration to avoid I2C bus collisions
    ec_val = 0.0
    ph_val = 0.0
    if engine.running:
        ec_val = engine._last_ec
        ph_val = engine._last_ph
    else:
        try:
            ec_probe = hw.get_ec_probe()
            if ec_probe:
                ec_val = ec_probe.read() or 0.0
        except:
            pass
        try:
            ph_probe = hw.get_ph_probe()
            if ph_probe:
                ph_val = ph_probe.read() or 0.0
        except:
            pass
            
    return jsonify({
        'pump_status': pump_running,
        'pump': pump_running,  # Alias for frontend compatibility
        'measurement_status': engine.running,
        'running': engine.running,  # Alias for frontend compatibility
        'system_status': "Operational" if engine.running else "Idle",
        'ec': ec_val,
        'ph': ph_val
    })

@app.route('/start_pump', methods=['POST'])
def start_pump():
    """Start the pump manually."""
    try:
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
    """Stop the pump manually."""
    try:
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

@app.route('/api/titrate/start', methods=['POST'])
def start_titration_api():
    try:
        data = request.json
        mode = data.get('mode', 'volumetric') # 'volumetric' or 'endpoint'
        exp_name = data.get('experiment_name', 'Test')
        project = data.get('project_name', 'Default')
        
        # Params
        params = {
            'sensor': data.get('sensor', 'ec')
        }

        # Thread Target Wrapper
        def run_task():
            try:
                if mode == 'volumetric':
                    volume = float(data.get('volume_ml', 60))
                    engine.run_volumetric(exp_name, volume, project, params)
                elif mode == 'endpoint':
                    target = float(data.get('target_ph', 7.0))
                    engine.run_endpoint(exp_name, target, project, params)
            except Exception as e:
                logger.error(f"Threaded titration error: {e}")

        # Check if already running
        if engine.running:
            return jsonify({'error': 'Titration already in progress'}), 400

        # Start Thread (non-daemon for clean shutdown)
        global _active_thread
        _active_thread = threading.Thread(target=run_task, daemon=False)
        _active_thread.start()

        return jsonify({'message': 'Titration started'})
        
    except Exception as e:
        logger.error(f"Titration start failed: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/titrate/data', methods=['GET'])
def get_titration_data():
    if not engine.running and not engine.current_data:
        return jsonify({})
    return jsonify(engine.current_data)

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

@app.route('/api/titrations/<path:filename>', methods=['DELETE'])
def delete_file_api(filename):
    # filename is like "ProjectName/file.csv"
    base_dir = os.path.join(app.root_path, 'static', 'titrations')
    full_path = os.path.join(base_dir, filename)
    
    # Path Traversal Check
    if not os.path.commonprefix([os.path.abspath(full_path), os.path.abspath(base_dir)]) == os.path.abspath(base_dir):
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
        for k, v in data.items():
            update_setting(k, v)
        return jsonify({'message': 'Settings saved'})
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
    hw = get_hardware()
    ec_probe = hw.get_ec_probe()
    if ec_probe:
        try:
            val = ec_probe.read()
            return jsonify({'conductivity': val})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return jsonify({'conductivity': 0.0}) # Or error if critical

@app.route('/start_conductivity_calibration', methods=['POST'])
def start_conductivity_calibration():
    try:
        data = request.json
        cal_type = data.get('type')
        value = data.get('value')
        
        hw = get_hardware()
        ec_probe = hw.get_ec_probe()
        
        if not ec_probe:
            return jsonify({'error': 'Conductivity probe not initialized'}), 500
            
        # Convert value to string if present, as Atlas expects strings in some cases or we fmt it
        # Probes.py handles formatting.
        
        response = ec_probe.calibrate(cal_type, value)
        
        # Update Last Calibration Date
        update_setting('LAST_CALIBRATION_DATE', datetime.datetime.now().strftime('%Y-%m-%d'))
        
        return jsonify({'message': f'Calibration command sent: {response}'})
    except Exception as e:
        logger.error(f"Calibration error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/start_pump_duration', methods=['POST'])
def start_pump_duration():
    try:
        data = request.json
        duration = float(data.get('duration', 0))
        if duration <= 0:
            return jsonify({'error': 'Invalid duration'}), 400
            
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

@app.route('/update_flow_rate', methods=['POST'])
def update_flow_rate_api():
    try:
        data = request.json
        flow_rate = data.get('flow_rate')
        if flow_rate:
            update_setting('DEFAULT_FLOW_RATE', float(flow_rate))
            update_setting('LAST_CALIBRATION_DATE', datetime.datetime.now().strftime('%Y-%m-%d'))
            return jsonify({'message': 'Flow rate updated'})
        return jsonify({'error': 'No flow rate provided'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download/<path:filename>')
def download_file(filename):
    # Path traversal protection
    base_dir = os.path.join(app.root_path, 'static', 'titrations')
    full_path = os.path.join(base_dir, filename)
    
    if not os.path.commonprefix([os.path.abspath(full_path), os.path.abspath(base_dir)]) == os.path.abspath(base_dir):
        abort(400, description='Invalid path')
    
    return send_from_directory('static/titrations', filename)

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
