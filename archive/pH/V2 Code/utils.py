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




def measure_ph():
    
        measurements = []  # Clear previous measurements
        max_attempts = 9999999999  # Maximum number of readings to prevent infinite loops
        stability_threshold = 0.009  # Standard deviation threshold for stability
        window_size = 10  # Number of recent readings to check for stability
        consecutive_stable_count = 0  # Counter for consecutive stable windows
        required_stable_windows = 5  # Required number of consecutive stable windows
        
        for i in range(max_attempts):
            conductivity_data = conductivity_probe.read_conductivity()
            print(f"Raw Conductivity Data: '{conductivity_data}' (Iteration {i+1})")
            cleaned_data = conductivity_data.strip().replace('\x00', '')
            conductivity_value = float(cleaned_data)
            print(f"Valid Conductivity Value: {conductivity_value}")
                
            # Simulating pH data based on conductivity for visualization
            ph_value = round(conductivity_value, 3) # Example formula
            measurements.append({'time': i, 'ph': ph_value})

            if len(measurements) >= window_size:
                recent_measurements = [m['ph'] for m in measurements[-window_size:]]
                std_dev = np.std(recent_measurements)
                print(f"Recent Measurements: {recent_measurements}, Standard Deviation: {std_dev}")
                
                if std_dev < stability_threshold:
                    consecutive_stable_count += 1
                    print(f"Stable window count: {consecutive_stable_count}/{required_stable_windows}")
                    
                    if consecutive_stable_count >= required_stable_windows:
                        print("Measurement stabilized for three consecutive windows.")
                        return np.mean(recent_measurements),std_dev
                    
                else:
                    consecutive_stable_count = 0  # Reset if instability is detected
            
            time.sleep(2.5)  # Wait before next measurement



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
            
def start_titration(experiment_name, pH, timestamp):
    """Start titration process and save real-time conductivity data using 10-second averages."""
    data = {
        'Volume (mL)': [],
        'Average pH': [],
        'Standard Deviation': []
    }
    # Read conductivity for 10 seconds (average and standard deviation)
    avg_conductivity, std_dev_conductivity = measure_ph()
    print(f"starting pH is {avg_conductivity}")
    print(f"pH to reach {pH}")    
    i=0
    try:
        while(avg_conductivity>=pH):
            print(f"pH is less than {pH}")
            # Read conductivity for 10 seconds (average and standard deviation)
            avg_conductivity, std_dev_conductivity = measure_ph()
            
            print(f"MEASURED pH is {avg_conductivity}")
            print(f"Data type of pH is {type(avg_conductivity)}")
            
            # Append the volume, average conductivity, and standard deviation to the data
            data['Volume (mL)'].append((i)* 0.5)
            print(f"{data}")
            data['Average pH'].append(float(avg_conductivity))
            print(f"{data}")
            data['Standard Deviation'].append(float(std_dev_conductivity))
            print(f"{data}")
            print(f"Volume: {(i)*0.5} mL, pH: {avg_conductivity} µS/cm, Std Dev: {std_dev_conductivity} ")
            if (avg_conductivity<pH):
                print(f"pH is greater than{pH}")
                break
            # Turn the pump on to titrate 0.5  mL
            switch_pump(False)
            time.sleep(PUMP_TIME_PER_ML)  # Pump for the time required for 0.5  mL
            switch_pump(True)
            i=i+1
            time.sleep(60)

            time.sleep(1)  # Optional: small delay between increments

        # Save the collected real titration data to a CSV file
        df = pd.DataFrame(data)
        filename = f'{experiment_name}_{timestamp}.csv'
        filepath = os.path.join('static', 'titrations', filename)
        df.to_csv(filepath, index=False)
        print(f"Titration data saved to {filepath}")
        
        return filename, datetime.datetime.now().strftime('%Y-%m-%d'), datetime.datetime.now().strftime('%H:%M:%S')

    except Exception as e:
        print(f'Error during titration: {str(e)}')
    finally:
        stop_pump()  # Ensure the pump is stopped immediately after titration

    






