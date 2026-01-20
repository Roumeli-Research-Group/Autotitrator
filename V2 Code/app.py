import subprocess
from flask import Flask, render_template, request, jsonify, send_from_directory
from utils import start_pump, stop_pump, start_titration, start_measurement, get_pump_status
from conductivity_probe import ConductivityProbe
import os
import datetime
import time
import RPi.GPIO as GPIO
import numpy as np
import re
import json

# Initialize Flask app
app = Flask(__name__)

# Initialize the conductivity probe
conductivity_probe = ConductivityProbe()

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
    titrations_files = []

    for f in os.listdir(titrations_directory):
        if f.endswith('.csv'):
            try:
                match = re.search(r'_(\d{8}_\d{6})\.csv$', f)
                if match:
                    timestamp = match.group(1)
                    date_time_obj = datetime.datetime.strptime(timestamp, '%Y%m%d_%H%M%S')
                    date = date_time_obj.strftime('%Y-%m-%d')
                    time = date_time_obj.strftime('%H:%M:%S')
                    experiment_name = f[:f.rfind(f'_{timestamp}')]
                    titrations_files.append({'filename': f, 'experiment_name': experiment_name, 'date': date, 'time': time})
            except ValueError:
                continue

    return render_template('database.html', titrations=titrations_files)
    
@app.route('/conductivity_probe_calibration')
def conductivity_probe_calibration():
    return render_template('conductivity_probe_calibration.html')


@app.route('/download/<filename>')
def download_file(filename):
    try:
        return send_from_directory('static/titrations', filename)
    except Exception:
        return jsonify({'error': 'File download failed'}), 500

@app.route('/start_conductivity_titration', methods=['POST'])
def start_conductivity_titration():
    global measurement_status
    try:
        measurement_status = True
        experiment_name = request.form.get('experiment_name', 'titration')
        volume_ml = float(request.form.get('volume_ml', 60))  # Default to 60 mL
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

        # Start the titration process
        filename, date, time_now, total_duration = start_titration(experiment_name, volume_ml, timestamp)

        # After titration, start the measurement process
        measurement_filename = start_measurement(experiment_name, timestamp)

        return jsonify({'message': 'Titration and measurement completed', 'filename': measurement_filename, 'date': date, 'time': time_now, 'total_duration': total_duration})

    except Exception as e:
        return jsonify({'error': f'Failed to complete titration and measurement: {str(e)}'}), 500

    finally:
        measurement_status = False
        stop_pump()

@app.route('/get_conductivity_data', methods=['GET'])
def get_conductivity_data():
    """Fetch real-time conductivity data."""
    try:
        conductivity = conductivity_probe.read_conductivity()
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
        
@app.route('/start_conductivity_calibration', methods=['POST'])
def start_conductivity_calibration():
    """Start 3-point calibration for the conductivity probe."""
    try:
        # Perform dry calibration
        dry_response = conductivity_probe.calibrate('dry')
        # Perform low point calibration (example: 84 µS/cm)
        low_response = conductivity_probe.calibrate('low', 84)
        # Perform high point calibration (example: 12880 µS/cm)
        high_response = conductivity_probe.calibrate('high', 1413)
        
        # Save calibration data (timestamp, dry, low, high responses)
        calibration_data = {
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'dry': dry_response,
            'low': low_response,
            'high': high_response
        }
        
        # Save the calibration data to a JSON file
        with open('calibration_data.json', 'w') as f:
            json.dump(calibration_data, f)
        
        return jsonify({'message': 'Calibration successful', 'data': calibration_data})
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
        measurements = []
        
        # Take 10 measurements, once per second
        for i in range(10):
            conductivity_data = conductivity_probe.read_conductivity()
            
            # Debugging: Print the raw data and its type before processing
            print(f"Raw Conductivity Data: '{conductivity_data}' (Iteration {i+1})")
            print(f"Type of Conductivity Data: {type(conductivity_data)}")

            try:
                # Ensure the data is a clean string by stripping whitespace and hidden characters
                cleaned_data = conductivity_data.strip().replace('\x00', '')  # Removing any null characters

                # Attempt conversion to float
                conductivity_value = float(cleaned_data)
                print(f"Valid Conductivity Value: {conductivity_value}")
                measurements.append(conductivity_value)  # Append valid readings, including 0.00
            except ValueError:
                print(f"Skipping invalid conductivity data: '{conductivity_data}'")  # Skip invalid readings

            time.sleep(1)  # Wait for 1 second between measurements

        # Check what was collected in the measurements list
        print(f"Collected Measurements: {measurements}")

        # Calculate average and standard deviation if there are valid measurements
        if measurements:
            average_conductivity = np.mean(measurements)
            std_dev_conductivity = np.std(measurements)
            print(f"Average Conductivity: {average_conductivity}, Standard Deviation: {std_dev_conductivity}")
        else:
            average_conductivity = None
            std_dev_conductivity = None
            print("No valid measurements were collected.")

        # Return the results as a JSON response
        return jsonify({
            'average_conductivity': average_conductivity,
            'std_dev_conductivity': std_dev_conductivity,
        })
    except Exception as e:
        return jsonify({'error': f'Error measuring conductivity: {str(e)}'}), 500




@app.route('/status')
def status():
    global measurement_status
    pump_status = get_pump_status()
    return jsonify({'pump_status': pump_status, 'measurement_status': measurement_status})

if __name__ == '__main__':
    try:
        app.run(debug=True, host='0.0.0.0')
    finally:
        GPIO.cleanup()

