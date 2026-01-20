import time
from AtlasI2C import AtlasI2C

class ConductivityProbe:
    def __init__(self, address=0x63):
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
