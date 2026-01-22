
from abc import ABC, abstractmethod
import logging

# Configure logging
logger = logging.getLogger(__name__)

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    logger.warning("RPi.GPIO not found. Real pump mode will fail.")

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

class RealPump(PumpInterface):
    """Real pump controller using GPIO relay."""
    
    def __init__(self, relay_pin=23):
        self.relay_pin = relay_pin
        
        if not GPIO_AVAILABLE:
            raise RuntimeError("GPIO not available for RealPump")
        
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.relay_pin, GPIO.OUT)
        # Initialize to HIGH (OFF for many relay boards)
        GPIO.output(self.relay_pin, GPIO.HIGH)
        
        
    def start(self):
        # Active LOW
        GPIO.output(self.relay_pin, GPIO.LOW)
        logger.info("Pump STARTED")

    def stop(self):
        GPIO.output(self.relay_pin, GPIO.HIGH)
        logger.info("Pump STOPPED")

    def get_status(self):
        # Returns True if Pump is ON (LOW)
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
