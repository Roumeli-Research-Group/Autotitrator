import time
from AtlasI2C import AtlasI2C

class ConductivityProbe:
    def __init__(self, address=0x64):
        """Initialize the conductivity probe."""
        self.probe = AtlasI2C(address=address)

    def read_conductivity(self):
        """Take a single conductivity reading."""
        try:
            reading = self.probe.query("R")
            reading_1= reading.split(':')[1].strip()
            print(f"Conductivity: {reading_1}")
            return reading_1
        except Exception as e:
            print(f"Error reading conductivity: {e}")

    def calibrate(self, calibration_type, value=None):
        """Perform calibration based on type: dry, low, high, or clear."""
        try:
            if calibration_type == 'dry':
                response = self.probe.query("Cal,dry")
            elif calibration_type == 'low' and value:
                response = self.probe.query(f"Cal,low,{value}")
            elif calibration_type == 'high' and value:
                response = self.probe.query(f"Cal,high,{value}")
            elif calibration_type == 'clear':
                response = self.probe.query("Cal,clear")
            else:
                print("Invalid calibration type or missing value.")
                return
            print(f"Calibration response: {response}")
        except Exception as e:
            print(f"Error during calibration: {e}")

    def set_temperature(self, temperature):
        """Set the temperature compensation."""
        try:
            response = self.probe.query(f"T,{temperature}")
            print(f"Temperature compensation set to: {temperature}°C")
            print(f"Response: {response}")
        except Exception as e:
            print(f"Error setting temperature: {e}")

    def get_status(self):
        """Get the status of the EZO-EC sensor."""
        try:
            status = self.probe.query("Status")
            print(f"Sensor Status: {status}")
            return status
        except Exception as e:
            print(f"Error retrieving status: {e}")

    def sleep(self):
        """Put the EZO-EC sensor to sleep."""
        try:
            response = self.probe.query("Sleep")
            print("Sensor is now in sleep mode.")
        except Exception as e:
            print(f"Error putting sensor to sleep: {e}")

    def start_continuous_reading(self, interval=1):
        """Start continuous reading mode at specified intervals (in seconds)."""
        try:
            response = self.probe.query(f"C,{interval}")
            print(f"Continuous reading started with interval: {interval} seconds")
            return response
        except Exception as e:
            print(f"Error starting continuous reading: {e}")

    def stop_continuous_reading(self):
        """Stop continuous reading mode."""
        try:
            response = self.probe.query("C,0")
            print("Continuous reading stopped.")
            return response
        except Exception as e:
            print(f"Error stopping continuous reading: {e}")

    def read_continuous_data(self, duration=10):
        """Read data continuously for a specified duration."""
        try:
            self.start_continuous_reading()
            start_time = time.time()

            while time.time() - start_time < duration:
                reading = self.probe.query("R")
                print(f"Continuous Reading: {reading}")
                time.sleep(1)  # Adjust sleep duration as needed

            self.stop_continuous_reading()
        except Exception as e:
            print(f"Error during continuous reading: {e}")

if __name__ == "__main__":
    probe = ConductivityProbe()

    # Example usage
    probe.read_conductivity()  # Take a single reading
    probe.calibrate('dry')  # Perform dry calibration
    probe.calibrate('low', 12880)  # Perform low point calibration
    probe.set_temperature(25.0)  # Set temperature compensation
    probe.get_status()  # Get sensor status
    probe.read_continuous_data(duration=15)  # Read data continuously for 15 seconds
    probe.sleep()  # Put the sensor to sleep
