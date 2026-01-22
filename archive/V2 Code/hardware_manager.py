# Hardware Manager for Autotitrator
import os
import time
import random
import logging
from abc import ABC, abstractmethod

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Try to import hardware libraries, but don't fail if they are missing (will fallback to mock if configured or forced)
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    logger.warning("RPi.GPIO not found. Real hardware mode will fail if selected.")

try:
    from AtlasI2C import AtlasI2C
    ATLAS_I2C_AVAILABLE = True
except ImportError:
    ATLAS_I2C_AVAILABLE = False
    logger.warning("AtlasI2C not found. Real hardware mode will fail if selected.")


class PumpInterface(ABC):
    @abstractmethod
    def start(self):
        pass

    @abstractmethod
    def stop(self):
        pass

    @abstractmethod
    def get_status(self):
        pass

class ProbeInterface(ABC):
    @abstractmethod
    def read_conductivity(self):
        pass

    @abstractmethod
    def calibrate(self, cal_type, value=None):
        pass
    
    @abstractmethod
    def sleep(self):
        pass


class RealPump(PumpInterface):
    def __init__(self, relay_pin=23, pump_pin=23):
        self.relay_pin = relay_pin
        self.pump_pin = pump_pin
        if not GPIO_AVAILABLE:
            raise RuntimeError("GPIO not available for RealPump")
        
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.relay_pin, GPIO.OUT)
        # Initialize to HIGH (OFF for many relay boards, but verify logic)
        # Original code said: GPIO.output(PUMP_PIN, GPIO.HIGH) is stop
        GPIO.output(self.relay_pin, GPIO.HIGH)
        
    def start(self):
        # Original code: GPIO.output(PUMP_PIN, GPIO.LOW) is start
        GPIO.output(self.relay_pin, GPIO.LOW)
        logger.info("Pump STARTED")

    def stop(self):
        GPIO.output(self.relay_pin, GPIO.HIGH)
        logger.info("Pump STOPPED")

    def get_status(self):
        # Original code: return GPIO.input(PUMP_PIN) == GPIO.LOW
        # If LOW is ON, return True (Pump is ON)
        return GPIO.input(self.relay_pin) == GPIO.LOW

class MockPump(PumpInterface):
    def __init__(self):
        self._is_running = False
        logger.info("MockPump initialized")

    def start(self):
        self._is_running = True
        logger.info("[MOCK] Pump STARTED")

    def stop(self):
        self._is_running = False
        logger.info("[MOCK] Pump STOPPED")

    def get_status(self):
        return self._is_running


class RealProbe(ProbeInterface):
    def __init__(self, address=0x64):
        if not ATLAS_I2C_AVAILABLE:
            raise RuntimeError("AtlasI2C not available for RealProbe")
        self.device = AtlasI2C(address=address)
        # Test connection
        try:
            self.device.write("Status")
        except Exception as e:
            logger.error(f"Failed to connect to AtlasI2C probe: {e}")
            raise

    def read_conductivity(self):
        try:
            response = self.device.query("R")
            # Response format example: "Success 1.5" or just "1.5" depending on glitch handler?
            # Creating a robust parser based on original code:
            # reading = self.probe.query("R")
            # reading_1= reading.split(':')[1].strip()
            # The AtlasI2C class in codebase returns "Success ... : data" or similar?
            # Let's look at the original conductivity_probe.py logic:
            # reading = self.probe.query("R") -> reading.split(':')[1].strip()
            # The AtlasI2C.py returns: "Success " + info + ": " + data
            
            parts = response.split(':')
            if len(parts) > 1:
                val = parts[1].strip()
                # Clean up null bytes if any
                val = val.replace('\x00', '')
                return float(val)
            else:
                logger.warning(f"Unexpected probe response format: {response}")
                return 0.0
        except Exception as e:
            logger.error(f"Error reading probe: {e}")
            return 0.0

    def calibrate(self, cal_type, value=None):
        cmd = ""
        if cal_type == 'dry':
            cmd = "Cal,dry"
        elif cal_type == 'low' and value:
            cmd = f"Cal,low,{value}"
        elif cal_type == 'high' and value:
            cmd = f"Cal,high,{value}"
        elif cal_type == 'clear':
            cmd = "Cal,clear"
        else:
            raise ValueError("Invalid calibration parameters")
        
        return self.device.query(cmd)

    def sleep(self):
        return self.device.query("Sleep")

class MockProbe(ProbeInterface):
    def __init__(self):
        logger.info("MockProbe initialized")

    def read_conductivity(self):
        # Simulate a fluctuating reading
        val = 1000 + random.uniform(-50, 50)
        logger.info(f"[MOCK] Read Conductivity: {val:.2f}")
        return val

    def calibrate(self, cal_type, value=None):
        logger.info(f"[MOCK] Calibrate {cal_type} with {value}")
        return "Success [MOCK]"

    def sleep(self):
        logger.info("[MOCK] Probe sleeping")

class HardwareManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(HardwareManager, cls).__new__(cls)
            cls._instance.init_hardware()
        return cls._instance

    def init_hardware(self):
        # Check environment variable or default to Mock if drivers missing
        # We can use an env var 'TITRATOR_ENV' = 'PROD' or 'DEV'
        env = os.environ.get('TITRATOR_ENV', 'DEV')
        
        logger.info(f"Initializing Hardware Manager in {env} mode")

        if env == 'PROD':
            try:
                self.pump = RealPump()
            except Exception as e:
                logger.error(f"Failed to init RealPump: {e}. Falling back to Mock.")
                self.pump = MockPump()

            try:
                self.probe = RealProbe()
            except Exception as e:
                logger.error(f"Failed to init RealProbe: {e}. Falling back to Mock.")
                self.probe = MockProbe()
        else:
            self.pump = MockPump()
            self.probe = MockProbe()

    def get_pump(self):
        return self.pump

    def get_probe(self):
        return self.probe

# Global instance helper
_hw_manager = None
def get_hardware():
    global _hw_manager
    if _hw_manager is None:
        _hw_manager = HardwareManager()
    return _hw_manager
