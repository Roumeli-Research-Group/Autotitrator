
import os
import logging
import atexit
import threading
from .pump import RealPump, MockPump
from .probes import PHProbe, ConductivityProbe, TemperatureProbe, MockProbe

# Configure logging
logger = logging.getLogger(__name__)

class HardwareManager:
    """Singleton hardware manager for pump and probes."""
    
    def __init__(self):
        self.env = None
        self.pump = None
        self.ph_probe = None
        self.ec_probe = None
        self.temp_probe = None
        # Serializes every I2C transaction. Each Atlas read blocks the bus
        # ~2s; two threads interleaving transactions corrupt each other's
        # responses (error 254 / garbage). Every probe read/command anywhere
        # in the app must hold this lock.
        self.bus_lock = threading.Lock()

    def init_hardware(self):
        self.env = os.environ.get('TITRATOR_ENV', 'DEV')
        logger.info(f"Initializing Hardware Manager in {self.env} mode")
        
        # Register Cleanup
        atexit.register(self.cleanup)

        # Initialize Pump
        if self.env == 'PROD':
            try:
                self.pump = RealPump()
            except Exception as e:
                logger.error(f"Failed to init RealPump: {e}. Falling back to Mock.")
                self.pump = MockPump()
        else:
            self.pump = MockPump()

        # Initialize Probes
        self.ph_probe = None
        self.ec_probe = None
        self.temp_probe = None

        if self.env == 'PROD':
            try:
                self.ph_probe = PHProbe(address=0x63)
            except Exception as e:
                logger.error(f"Failed to init PHProbe: {e}")

            try:
                self.ec_probe = ConductivityProbe(address=0x64)
            except Exception as e:
                logger.error(f"Failed to init ConductivityProbe: {e}")

            try:
                self.temp_probe = TemperatureProbe(address=0x66)
            except Exception as e:
                logger.error(f"Failed to init TemperatureProbe: {e}")
        else:
            self.ph_probe = MockProbe("PH")
            self.ec_probe = MockProbe("EC")
            self.temp_probe = MockProbe("TEMP")

    def get_pump(self):
        return self.pump

    def get_ph_probe(self):
        if not self.ph_probe and self.env == 'PROD':
             # Try lazy loading again if it failed initially? OR just return None
             pass
        return self.ph_probe

    def get_ec_probe(self):
        return self.ec_probe

    def get_temp_probe(self):
        return self.temp_probe

    def get_probe(self, probe_type):
        ptype = probe_type.lower()
        if ptype == 'ph':
            return self.ph_probe
        elif ptype in ('ec', 'conductivity'):
            return self.ec_probe
        elif ptype in ('temp', 'rtd', 'temperature'):
            return self.temp_probe
        return None

    def cleanup(self):
        logger.info("Cleaning up hardware resources...")
        if self.pump:
            try:
                self.pump.stop()
            except Exception as e:
                logger.error(f"Error stopping pump during cleanup: {e}")
        
        # Attempt GPIO cleanup if available
        try:
            import RPi.GPIO as GPIO
            GPIO.cleanup()
            logger.info("GPIO cleanup complete.")
        except ImportError:
            pass # Not on Pi
        except Exception as e:
            logger.error(f"Error during GPIO cleanup: {e}")

# Global instance helper (singleton pattern)
_hw_manager = None

def get_hardware():
    """Get the singleton HardwareManager instance."""
    global _hw_manager
    if _hw_manager is None:
        _hw_manager = HardwareManager()
        _hw_manager.init_hardware()
    return _hw_manager

