
from flask import Flask, render_template, request, jsonify, send_from_directory
from utils import start_pump, stop_pump, start_titration, start_measurement, get_pump_status, set_flow_rate, read_conductivity_for_10_seconds
from hardware_manager import get_hardware
import os
import datetime
import time
import numpy as np
import re
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Global variables to track the pump and measurement status
measurement_status = False

@app.route('/')
def landing_page():
    return render_template('index.html')

@app.route('/measurement')
def measurement():
    return render_template('measurement.html')

@app.route('/calibration')
def calibration():
    return render_template('calibration.html')

@app.route('/pump_calibration')
def pump_calibration():
    return render_template('pump_calibration.html')

@app.route('/database')
def database():
    titrations_directory = os.path.join(app.root_path, 'static', 'titrations')
    # Ensure directory exists
    os.makedirs(titrations_directory, exist_ok=True)
    
    titrations_files = []

    # Walk through all directories
    for root, dirs, files in os.walk(titrations_directory):
        for f in files:
            if f.endswith('.csv'):
                try:
                    match = re.search(r'_(\d{8}_\d{6})\.csv$', f)
                    if match:
                        timestamp = match.group(1)
                        date_time_obj = datetime.datetime.strptime(timestamp, '%Y%m%d_%H%M%S')
                        date = date_time_obj.strftime('%Y-%m-%d')
                        time_str = date_time_obj.strftime('%H:%M:%S')
                        
                        # Get relative path for download
                        full_path = os.path.join(root, f)
                        rel_path = os.path.relpath(full_path, titrations_directory)
                        
                        # Extract project name from folder structure
                        # If file is in static/titrations/MyProject/file.csv -> Project = MyProject
                        # If file is in static/titrations/file.csv -> Project = Default
                        project_name = os.path.basename(os.path.dirname(full_path))
                        if project_name == 'titrations':
                             project_name = 'Default'
                        
                        experiment_name = f[:f.rfind(f'_{timestamp}')]
                        titrations_files.append({
                            'filename': rel_path, # Store relative path for download
                            'experiment_name': experiment_name, 
                            'project_name': project_name,
                            'date': date, 
                            'time': time_str, 
                            'timestamp_obj': date_time_obj
                        })
                except ValueError:
                    continue

    # Sort by timestamp descending (newest first)
    titrations_files.sort(key=lambda x: x['timestamp_obj'], reverse=True)

    return render_template('database.html', titrations=titrations_files)
    
@app.route('/conductivity_probe_calibration')
def conductivity_probe_calibration():
    return render_template('conductivity_probe_calibration.html')


@app.route('/download/<path:filename>')
def download_file(filename):
    try:
        # filename acts as a path here because we use <path:filename> converter
        return send_from_directory('static/titrations', filename)
    except Exception:
        return jsonify({'error': 'File download failed'}), 500

@app.route('/start_conductivity_titration', methods=['POST'])
def start_conductivity_titration():
    global measurement_status
    try:
        measurement_status = True
        experiment_name = request.form.get('experiment_name', 'titration')
        project_name = request.form.get('project_name', 'Default')
        volume_ml = float(request.form.get('volume_ml', 60))
        
        # Input validation using settings
        import settings
        volume_ml = max(settings.MIN_VOLUME_ML, min(volume_ml, settings.MAX_VOLUME_ML))
        
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

        # Start the titration process
        # Note: This is a synchronous call. It will BLOCK the server until finished.
        filename, date, time_now, total_duration = start_titration(experiment_name, volume_ml, timestamp, project_name)

        # After titration, start the measurement process
        measurement_filename = start_measurement(experiment_name, timestamp, project_name)

        return jsonify({'message': 'Titration and measurement completed', 'filename': filename, 'date': date, 'time': time_now, 'total_duration': total_duration})

    except Exception as e:
        logger.error(f"Titration failed: {e}")
        return jsonify({'error': f'Failed to complete titration and measurement: {str(e)}'}), 500

    finally:
        measurement_status = False
        stop_pump()

@app.route('/get_conductivity_data', methods=['GET'])
def get_conductivity_data():
    """Fetch real-time conductivity data."""
    try:
        conductivity = get_hardware().get_probe().read_conductivity()
        # Ensure format consistency for frontend
        return jsonify({'conductivity': conductivity})
    except Exception as e:
        return jsonify({'error': f'Error fetching conductivity data: {str(e)}'}), 500

@app.route('/start_pump', methods=['POST'])
def start_pump_route():
    try:
        start_pump()
        return jsonify({'message': 'Pump started successfully'})
    except Exception as e:
        return jsonify({'error': f'Failed to start pump: {str(e)}'}), 500

@app.route('/stop_pump', methods=['POST'])
def stop_pump_route():
    try:
        stop_pump()
        return jsonify({'message': 'Pump stopped successfully'})
    except Exception as e:
        return jsonify({'error': f'Failed to stop pump: {str(e)}'}), 500

@app.route('/update_flow_rate', methods=['POST'])
def update_flow_rate():
    try:
        data = request.json
        flow_rate = data.get('flow_rate')
        if flow_rate is None:
             return jsonify({'error': 'Missing flow_rate'}), 400
        
        set_flow_rate(flow_rate)
        return jsonify({'message': f'Flow rate updated to {flow_rate} mL/s'})
    except Exception as e:
        return jsonify({'error': f'Failed to update flow rate: {str(e)}'}), 500

@app.route('/start_pump_duration', methods=['POST'])
def start_pump_duration():
    try:
        data = request.json
        duration = data.get('duration')
        if not duration:
            return jsonify({'error': 'Duration required'}), 400
            
        start_pump()
        time.sleep(float(duration))
        stop_pump()
        return jsonify({'message': f'Pump ran for {duration} seconds'})
    except Exception as e:
        stop_pump() # Safety stop
        return jsonify({'error': f'Failed to run pump: {str(e)}'}), 500
        
@app.route('/start_conductivity_calibration', methods=['POST'])
def start_conductivity_calibration():
    """Start split calibration for the conductivity probe."""
    try:
        data = request.json
        cal_type = data.get('type')
        value = data.get('value')

        if not cal_type:
            return jsonify({'error': 'Calibration type required'}), 400

        # Load existing calibration data or create new
        calibration_data = {}
        if os.path.exists('calibration_data.json'):
            try:
                with open('calibration_data.json', 'r') as f:
                    calibration_data = json.load(f)
            except json.JSONDecodeError:
                pass # Start fresh if corrupt

        # Update timestamp
        calibration_data['timestamp'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        response = get_hardware().get_probe().calibrate(cal_type, value)
        
        # Save response
        calibration_data[cal_type] = f"Response: {response}"
        
        # Save the calibration data to a JSON file
        with open('calibration_data.json', 'w') as f:
            json.dump(calibration_data, f)
        
        return jsonify({'message': f'{cal_type.capitalize()} calibration successful', 'response': str(response), 'data': calibration_data})
    except Exception as e:
        return jsonify({'error': f'Calibration failed: {str(e)}'}), 500

@app.route('/get_latest_calibrations', methods=['GET'])
def get_latest_calibrations():
    """Fetch the latest calibration data from file."""
    try:
        with open('calibration_data.json', 'r') as f:
            calibration_data = json.load(f)
        return jsonify(calibration_data)
    except FileNotFoundError:
        return jsonify({'error': 'No calibration data found.'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500
        
@app.route('/measure_conductivity', methods=['GET'])
def measure_conductivity():
    """Measure conductivity 10 times and return the average and standard deviation."""
    try:
        # Use the utility function to avoid duplication
        avg_conductivity, std_dev_conductivity = read_conductivity_for_10_seconds()

        # Return the results as a JSON response
        return jsonify({
            'average_conductivity': avg_conductivity,
            'std_dev_conductivity': std_dev_conductivity,
        })
    except Exception as e:
        return jsonify({'error': f'Error measuring conductivity: {str(e)}'}), 500

# --- Project Management API ---
PROJECTS_FILE = 'projects.json'

def get_projects_list():
    if not os.path.exists(PROJECTS_FILE):
        # Scan for existing folders to populate initial list if empty
        titrations_directory = os.path.join(app.root_path, 'static', 'titrations')
        existing = ["Default"]
        if os.path.exists(titrations_directory):
            for d in os.listdir(titrations_directory):
                if os.path.isdir(os.path.join(titrations_directory, d)):
                    if d not in existing:
                        existing.append(d)
        
        with open(PROJECTS_FILE, 'w') as f:
            json.dump(existing, f)
        return existing
        
    try:
        with open(PROJECTS_FILE, 'r') as f:
            return json.load(f)
    except:
        return ["Default"]

def save_projects_list(projects):
    with open(PROJECTS_FILE, 'w') as f:
        json.dump(projects, f)

@app.route('/projects', methods=['GET'])
def get_projects():
    return jsonify(get_projects_list())

@app.route('/projects', methods=['POST'])
def add_project():
    try:
        data = request.json
        new_project = data.get('name')
        if not new_project: return jsonify({'error': 'Project name required'}), 400
        
        # Sanitize
        safe_project = "".join([c for c in new_project if c.isalnum() or c in (' ', '_', '-')]).strip()
        if not safe_project: return jsonify({'error': 'Invalid project name'}), 400
        
        projects = get_projects_list()
        if safe_project not in projects:
            projects.append(safe_project)
            save_projects_list(projects)
            
        return jsonify({'message': 'Project added', 'projects': projects})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/projects', methods=['DELETE'])
def delete_project():
    try:
        data = request.json
        del_project = data.get('name')
        
        projects = get_projects_list()
        if del_project in projects:
            projects.remove(del_project)
            save_projects_list(projects)
            
        return jsonify({'message': 'Project removed', 'projects': projects})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/status')
def status():
    global measurement_status
    try:
        pump_status = get_pump_status()
    except:
        pump_status = False
        
    return jsonify({'pump_status': pump_status, 'measurement_status': measurement_status})

@app.route('/settings')
def settings_page():
    return render_template('settings.html')

# --- Settings API ---
@app.route('/api/settings', methods=['GET'])
def get_settings_api():
    """Get all current effective settings."""
    import settings
    from utils import get_setting
    
    # List of keys exposed to UI
    keys = [
        'DEFAULT_FLOW_RATE', 'VOLUME_PER_STEP',
        'TITRATION_WAIT_TIME', 'MEASUREMENT_INITIAL_WAIT', 
        'MEASUREMENT_STEP_WAIT', 'CONDUCTIVITY_READINGS',
        'CONDUCTIVITY_READ_DELAY'
    ]
    
    data = {}
    for k in keys:
        # Fallback to module constant if config missing
        default = getattr(settings, k)
        data[k] = get_setting(k, default)
        
    # Also include current flow rate which might be different if changed via pump cal
    # Actually FLOW_RATE from utils is authoritative
    from utils import FLOW_RATE
    data['FLOW_RATE'] = FLOW_RATE
        
    return jsonify(data)

@app.route('/api/settings', methods=['POST'])
def update_settings_api():
    """Update settings."""
    from utils import update_setting
    try:
        data = request.json
        for k, v in data.items():
            update_setting(k, v)
        return jsonify({'message': 'Settings saved'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings/reset', methods=['POST'])
def reset_settings_api():
    """Reset to system defaults by clearing config file."""
    try:
        from utils import save_config, config
        # We can just empty the config dictionary (except maybe some persistent things?)
        # For now, let's just clear everything as requested "Restore to Default"
        config.clear()
        save_config(config)
        
        # Explicitly reset FLOW_RATE to default
        import settings
        from utils import update_setting
        update_setting('FLOW_RATE', settings.DEFAULT_FLOW_RATE)
        
        return jsonify({'message': 'Settings restored to defaults'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Initialize hardware on startup
    get_hardware()
    # Run app
    app.run(debug=True, host='0.0.0.0')

