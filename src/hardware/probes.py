
from abc import ABC, abstractmethod
import logging
import random

# Configure logging
logger = logging.getLogger(__name__)

try:
    from .drivers.AtlasI2C import AtlasI2C
    ATLAS_I2C_AVAILABLE = True
except ImportError:
    ATLAS_I2C_AVAILABLE = False
    logger.warning("AtlasI2C driver not found. Real hardware mode will fail.")

class ProbeInterface(ABC):
    @abstractmethod
    def read(self):
        """Returns the primary reading (conductivity or pH)."""
        pass

    @abstractmethod
    def calibrate(self, cal_type, value=None):
        pass
    
    @abstractmethod
    def sleep(self):
        pass

class RealAtlasProbe(ProbeInterface):
    def __init__(self, address, name="AtlasProbe"):
        if not ATLAS_I2C_AVAILABLE:
            raise RuntimeError(f"AtlasI2C not available for {name}")
        self.device = AtlasI2C(address=address, name=name)
        self.name = name
        # Test connection
        try:
            self.device.write("Status")
        except Exception as e:
            logger.error(f"Failed to connect to {name} at {address}: {e}")
            raise

    def _parse_response(self, response):
        """Parses the AtlasI2C response format 'Success [Info]: [Data]'."""
        if not response or not isinstance(response, str):
            return 0.0
            
        if response.startswith("Error"):
            logger.warning(f"Probe Error: {response}")
            return 0.0

        try:
            # Expected format: "Success ... : <Data>"
            if ":" in response:
                # Take the last part to be safe
                val = response.rpartition(':')[2].strip()
                val = val.replace('\x00', '')
                if val:
                    return float(val)
            else:
                 # Try parsing direct logic
                 val = response.replace("Success", "").strip().replace('\x00', '')
                 if val: 
                     return float(val)
                     
            return 0.0
        except ValueError:
             logger.warning(f"Could not parse float from: {response}")
             return 0.0

    def read(self):
        """Read current value from probe. Returns None on error."""
        try:
            response = self.device.query("R")
            return self._parse_response(response)
        except Exception as e:
            logger.error(f"Error reading {self.name}: {e}")
            return None  # Return None to indicate error (not 0.0)

    def calibrate(self, cal_type, value=None):
        cmd = ""
        # Map generic calibration types to Atlas commands
        if cal_type == 'dry':
            cmd = "Cal,dry"
        elif cal_type == 'low' and value:
            cmd = f"Cal,low,{value}"
        elif cal_type == 'mid' and value: # pH 7
             cmd = f"Cal,mid,{value}"
        elif cal_type == 'high' and value:
            cmd = f"Cal,high,{value}"
        elif cal_type == 'clear':
            cmd = "Cal,clear"
        else:
            raise ValueError(f"Invalid calibration parameters for {cal_type}")
        
        return self.device.query(cmd)

    def sleep(self):
        return self.device.query("Sleep")


class ConductivityProbe(RealAtlasProbe):
    def __init__(self, address=0x64):
        super().__init__(address, name="Conductivity Probe")

class PHProbe(RealAtlasProbe):
    def __init__(self, address=0x63):
        super().__init__(address, name="pH Probe")


class MockProbe(ProbeInterface):
    def __init__(self, probe_type="EC"):
        self.type = probe_type
        logger.info(f"MockProbe ({probe_type}) initialized")
        self._val = 7.0 if probe_type == "PH" else 1000.0

    def read(self):
        """Simulate probe reading with bounded random walk."""
        change = random.uniform(-0.1, 0.1) if self.type == "PH" else random.uniform(-10, 10)
        self._val += change
        # Bound to realistic ranges
        if self.type == "PH":
            self._val = max(0.0, min(14.0, self._val))  # pH: 0-14
        else:
            self._val = max(0.0, min(100000.0, self._val))  # EC: 0-100000 µS/cm
        return self._val

    def calibrate(self, cal_type, value=None):
        logger.info(f"[MOCK] Calibrate {cal_type} with {value}")
        return "Success [MOCK]"

    def sleep(self):
        logger.info("[MOCK] Probe sleeping")
