
from abc import ABC, abstractmethod
import logging
import random
import time

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

    def set_temp_compensation(self, temp_c):
        """Send the solution temperature for compensated readings. No-op by default."""
        return None

    def get_health(self):
        """Return calibration/health info as a dict. Empty by default."""
        return {}

class RealAtlasProbe(ProbeInterface):
    def __init__(self, address, name="AtlasProbe"):
        if not ATLAS_I2C_AVAILABLE:
            raise RuntimeError(f"AtlasI2C not available for {name}")
        self.device = AtlasI2C(address=address, name=name)
        self.name = name
        # Test connection by querying device info (must read response to clear buffer)
        try:
            response = self.device.query("I")
            logger.info(f"{name} initialized at address {address}: {response}")
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

    def read(self, max_retries=2):
        """Read current value from probe. Returns None on error after retries."""
        last_error = None
        for attempt in range(max_retries):
            try:
                response = self.device.query("R")
                value = self._parse_response(response)
                # Valid reading (not 0.0 from error parsing)
                if value is not None and value != 0.0:
                    return value
                # Got 0.0 - might be parse error, retry
                if attempt < max_retries - 1:
                    time.sleep(0.5)
            except Exception as e:
                last_error = e
                logger.warning(f"Error reading {self.name} (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(0.5)
        
        if last_error:
            logger.error(f"Failed to read {self.name} after {max_retries} attempts: {last_error}")
        return None  # Return None to indicate error

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
        elif cal_type == 'point' and value is not None:
            # Single-point calibration (EZO-RTD): Cal,<known temp>
            cmd = f"Cal,{value}"
        elif cal_type == 'clear':
            cmd = "Cal,clear"
        else:
            raise ValueError(f"Invalid calibration parameters for {cal_type}")

        return self.device.query(cmd)

    def sleep(self):
        return self.device.query("Sleep")

    def set_temp_compensation(self, temp_c):
        """Send solution temperature (T,<t>) so the EZO circuit compensates readings."""
        response = self.device.query(f"T,{float(temp_c):.2f}")
        logger.info(f"{self.name}: temperature compensation set to {temp_c:.2f} C")
        return response

    def _query_field(self, command):
        """Run a query and return the payload after 'Success ...: ' (or None)."""
        try:
            response = self.device.query(command)
            if response and isinstance(response, str) and not response.startswith("Error"):
                if ":" in response:
                    return response.rpartition(':')[2].strip().replace('\x00', '')
                return response.replace("Success", "").strip().replace('\x00', '')
        except Exception as e:
            logger.warning(f"{self.name}: query '{command}' failed: {e}")
        return None

    def get_health(self):
        """Calibration point count from the EZO firmware (Cal,? -> '?Cal,N')."""
        health = {}
        cal = self._query_field("Cal,?")
        if cal:
            # Format: ?Cal,<n>
            try:
                health['cal_points'] = int(cal.split(',')[-1])
            except ValueError:
                health['cal_raw'] = cal
        return health


class ConductivityProbe(RealAtlasProbe):
    def __init__(self, address=0x64):
        super().__init__(address, name="Conductivity Probe")

    def get_health(self):
        health = super().get_health()
        k = self._query_field("K,?")  # cell constant, format ?K,<value>
        if k:
            try:
                health['k_value'] = float(k.split(',')[-1])
            except ValueError:
                pass
        return health

class PHProbe(RealAtlasProbe):
    def __init__(self, address=0x63):
        super().__init__(address, name="pH Probe")

    def get_health(self):
        """Adds electrode slope: ?Slope,<acid%>,<base%>,<offset mV>.
        Slopes near 100% indicate a healthy electrode; below ~90% the
        electrode is aging and should be cleaned or replaced."""
        health = super().get_health()
        slope = self._query_field("Slope,?")
        if slope:
            parts = slope.split(',')
            try:
                # parts like ['?SLOPE', '99.7', '100.3', '-0.32']
                nums = [float(p) for p in parts if _is_float(p)]
                if len(nums) >= 2:
                    health['slope_acid_pct'] = nums[0]
                    health['slope_base_pct'] = nums[1]
                if len(nums) >= 3:
                    health['offset_mv'] = nums[2]
            except ValueError:
                health['slope_raw'] = slope
        return health

def _is_float(s):
    try:
        float(s)
        return True
    except ValueError:
        return False

class TemperatureProbe(RealAtlasProbe):
    """Atlas EZO-RTD temperature circuit (default address 0x66)."""
    def __init__(self, address=0x66):
        super().__init__(address, name="Temperature Probe")

    def read(self, max_retries=2):
        value = super().read(max_retries)
        # RTD firmware reports -1023.000 when no sensor is attached
        if value is not None and value <= -1000:
            logger.warning("Temperature probe reports no RTD sensor attached (-1023)")
            return None
        return value


class MockProbe(ProbeInterface):
    # type -> (start value, max step, lower bound, upper bound)
    _PROFILES = {
        "PH":   (7.0,    0.1,  0.0, 14.0),
        "EC":   (1000.0, 10.0, 0.0, 100000.0),
        "TEMP": (22.0,   0.05, 0.0, 100.0),
    }

    def __init__(self, probe_type="EC"):
        self.type = probe_type
        logger.info(f"MockProbe ({probe_type}) initialized")
        start, self._step, self._lo, self._hi = self._PROFILES.get(
            probe_type, self._PROFILES["EC"])
        self._val = start

    def read(self):
        """Simulate probe reading with bounded random walk."""
        self._val += random.uniform(-self._step, self._step)
        self._val = max(self._lo, min(self._hi, self._val))
        return self._val

    def calibrate(self, cal_type, value=None):
        logger.info(f"[MOCK] Calibrate {cal_type} with {value}")
        return "Success [MOCK]"

    def sleep(self):
        logger.info("[MOCK] Probe sleeping")

    def set_temp_compensation(self, temp_c):
        logger.info(f"[MOCK] Temp compensation set to {temp_c:.2f} C")
        return "Success [MOCK]"

    def get_health(self):
        if self.type == "PH":
            return {'cal_points': 3, 'slope_acid_pct': 99.7,
                    'slope_base_pct': 100.3, 'offset_mv': -0.32}
        if self.type == "TEMP":
            return {'cal_points': 1}
        return {'cal_points': 2, 'k_value': 1.0}
