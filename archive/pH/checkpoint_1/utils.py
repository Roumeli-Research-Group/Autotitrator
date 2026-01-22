import os
import datetime
import numpy as np
import RPi.GPIO as GPIO
import time
import pandas as pd
from conductivity_probe import ConductivityProbe

# GPIO setup
GPIO.setmode(GPIO.BCM)  # Set the GPIO mode to BCM or GPIO.BOARD depending on your setup
GPIO.setwarnings(False)  # Disable warnings

RELAY_PIN = 23
PUMP_PIN = 23

GPIO.setup(RELAY_PIN, GPIO.OUT)
GPIO.setup(PUMP_PIN, GPIO.OUT)

# Flow rate in mL/s
FLOW_RATE =0.2245
PUMP_TIME_PER_ML = 0.5 / FLOW_RATE

# Initialize the conductivity probe
conductivity_probe = ConductivityProbe()

def start_pump():
    """Start the pump by setting GPIO pin LOW."""
    GPIO.output(RELAY_PIN, GPIO.LOW)
    GPIO.output(PUMP_PIN, GPIO.LOW)

def stop_pump():
    """Stop the pump by setting GPIO pin HIGH."""
    GPIO.output(RELAY_PIN, GPIO.HIGH)
    GPIO.output(PUMP_PIN, GPIO.HIGH)

def switch_pump(state):
    GPIO.output(PUMP_PIN, state)

def get_pump_status():
    """Get the current status of the pump based on GPIO state."""
    return GPIO.input(PUMP_PIN) == GPIO.LOW

def save_titration_data(filename, data):
    """Save titration data to a file."""
    file_path = f'static/titrations/{filename}'
    with open(file_path, 'w') as f:
        for line in data:
            f.write(','.join(map(str, line)) + '\n')
            
def start_titration(experiment_name, volume_ml, timestamp):
    """Start titration process and save real-time conductivity data using 10-second averages."""
    total_duration = volume_ml * PUMP_TIME_PER_ML  # Total time to pump entire volume
    data = {
        'Volume (mL)': [],
        'Average Conductivity (µS/cm)': [],
        'Standard Deviation (µS/cm)': []
    }

    try:
        for i in range(int(volume_ml)*2):
            # Turn the pump on to titrate 0.5  mL
            switch_pump(False)
            time.sleep(PUMP_TIME_PER_ML)  # Pump for the time required for 0.5  mL
            switch_pump(True)
            time.sleep(60)
            

            # Read conductivity for 10 seconds (average and standard deviation)
            avg_conductivity, std_dev_conductivity = read_conductivity_for_10_seconds()

            # Append the volume, average conductivity, and standard deviation to the data
            data['Volume (mL)'].append((i +1)* 0.5)
            data['Average Conductivity (µS/cm)'].append(avg_conductivity)
            data['Standard Deviation (µS/cm)'].append(std_dev_conductivity)

            print(f"Volume: {(i + 1)*0.5} mL, Average Conductivity: {avg_conductivity} µS/cm, Std Dev: {std_dev_conductivity} µS/cm")

            time.sleep(1)  # Optional: small delay between increments

        # Save the collected real titration data to a CSV file
        df = pd.DataFrame(data)
        filename = f'{experiment_name}_{timestamp}.csv'
        filepath = os.path.join('static', 'titrations', filename)
        df.to_csv(filepath, index=False)
        print(f"Titration data saved to {filepath}")

    except Exception as e:
        print(f'Error during titration: {str(e)}')
    finally:
        stop_pump()  # Ensure the pump is stopped immediately after titration

    return filename, datetime.datetime.now().strftime('%Y-%m-%d'), datetime.datetime.now().strftime('%H:%M:%S'), total_duration


def read_conductivity_for_10_seconds():
    """Read conductivity 10 times over 10 seconds, similar to measure_conductivity in app.py."""
    values = []
    start_time = time.time()

    # Take 10 measurements over 10 seconds
    for i in range(10):
        try:
            # Read conductivity from the probe
            conductivity_data = conductivity_probe.read_conductivity()
            
            # Debugging: Print the raw data and its type before processing
            print(f"Raw Conductivity Data: '{conductivity_data}' (Iteration {i+1})")
            print(f"Type of Conductivity Data: {type(conductivity_data)}")

            # Clean the data by stripping whitespace and hidden characters
            cleaned_data = conductivity_data.strip().replace('\x00', '')

            # Attempt to convert to float and append valid readings
            conductivity_value = float(cleaned_data)
            print(f"Valid Conductivity Value: {conductivity_value}")
            values.append(conductivity_value)
        except ValueError:
            # Skip invalid readings
            print(f"Skipping invalid conductivity data: '{conductivity_data}'")
        except Exception as e:
            print(f"Error reading conductivity: {e}")
        
        # Wait 1 second between measurements
        time.sleep(1)

    # Check collected values
    print(f"Collected Measurements: {values}")

    # Calculate the average and standard deviation of the collected values
    if values:
        avg_value = np.mean(values)
        std_dev = np.std(values)
        print(f"Average Conductivity: {avg_value}, Standard Deviation: {std_dev}")
    else:
        avg_value = None
        std_dev = None
        print("No valid measurements were collected.")

    return avg_value, std_dev


def start_measurement(experiment_name, timestamp):
    """Perform conductivity measurement after titration."""
    data = {
        'Measurement': [],
        'Average Conductivity': [],
        'Standard Deviation': []
    }

    # Wait for titration process to finish
    print("Waiting for titration to complete...")
    time.sleep(20)  # Adjust the wait time for stability if needed

    for measurement_num in range(1, 4):
        print(f"Starting measurement {measurement_num}...")
        time.sleep(120)  # Wait for 120 seconds before each measurement to stabilize

        # Perform the conductivity measurement
        avg_value, std_dev = read_conductivity_for_10_seconds()

        # Print for debugging
        print(f"Measurement {measurement_num}: Average Conductivity = {avg_value}, Std Dev = {std_dev}")

        data['Measurement'].append(measurement_num)
        data['Average Conductivity'].append(avg_value)
        data['Standard Deviation'].append(std_dev)

    df = pd.DataFrame(data)
    filename = f'{experiment_name}_measurement_{timestamp}.csv'
    filepath = os.path.join('static', 'measurements', filename)

    # Save the data to a CSV file
    df.to_csv(filepath, index=False)
    print(f"Data saved to {filepath}")

    return filename


