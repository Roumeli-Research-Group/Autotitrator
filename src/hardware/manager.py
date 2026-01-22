
import os
import logging
import atexit
from .pump import RealPump, MockPump
from .probes import PHProbe, ConductivityProbe, MockProbe

# Configure logging
logger = logging.getLogger(__name__)

class HardwareManager:
    """Singleton hardware manager for pump and probes."""
    
    def __init__(self):
        self.env = None
        self.pump = None
        self.ph_probe = None
        self.ec_probe = None

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

        if self.env == 'PROD':
            try:
                self.ph_probe = PHProbe(address=0x63)
            except Exception as e:
                logger.error(f"Failed to init PHProbe: {e}")
                
            try:
                self.ec_probe = ConductivityProbe(address=0x64)
            except Exception as e:
                logger.error(f"Failed to init ConductivityProbe: {e}")
        else:
            self.ph_probe = MockProbe("PH")
            self.ec_probe = MockProbe("EC")

    def get_pump(self):
        return self.pump

    def get_ph_probe(self):
        if not self.ph_probe and self.env == 'PROD':
             # Try lazy loading again if it failed initially? OR just return None
             pass
        return self.ph_probe

    def get_ec_probe(self):
        return self.ec_probe
        
    def get_probe(self, probe_type):
        if probe_type.lower() == 'ph':
            return self.ph_probe
        elif probe_type.lower() == 'ec' or probe_type.lower() == 'conductivity':
            return self.ec_probe
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

