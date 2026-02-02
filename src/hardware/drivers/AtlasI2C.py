#!/usr/bin/python

import io
import sys
import fcntl
import time
import copy
import string


class AtlasI2C:
    """Atlas Scientific I2C device driver."""

    # the timeout needed to query readings and calibrations
    # EC calibration requires 600ms, using 2.0s for safety margin
    LONG_TIMEOUT = 2.0
    # timeout for regular commands
    SHORT_TIMEOUT = .3
    # the default bus for I2C on the newer Raspberry Pis, 
    # certain older boards use bus 0
    DEFAULT_BUS = 1
    # the default address for the sensor
    DEFAULT_ADDRESS = 98
    LONG_TIMEOUT_COMMANDS = ("R", "CAL")
    SLEEP_COMMANDS = ("SLEEP", )

    def __init__(self, address=None, moduletype="", name="", bus=None):
        """
        Open two file streams, one for reading and one for writing.
        The specific I2C channel is selected with bus (usually 1).
        """
        self._address = address or self.DEFAULT_ADDRESS
        self.bus = bus or self.DEFAULT_BUS
        self._long_timeout = self.LONG_TIMEOUT
        self._short_timeout = self.SHORT_TIMEOUT
        self._name = name
        self._module = moduletype
        self.file_read = None
        self.file_write = None
        
        try:
            self.file_read = io.open(file=f"/dev/i2c-{self.bus}", 
                                     mode="rb", 
                                     buffering=0)
            self.file_write = io.open(file=f"/dev/i2c-{self.bus}",
                                      mode="wb", 
                                      buffering=0)
            self.set_i2c_address(self._address)
        except Exception:
            # Clean up file handles if initialization fails
            self.close()
            raise

    @property
    def long_timeout(self):
        return self._long_timeout

    @property
    def short_timeout(self):
        return self._short_timeout

    @property
    def name(self):
        return self._name
        
    @property
    def address(self):
        return self._address
        
    @property
    def moduletype(self):
        return self._module
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures cleanup."""
        self.close()
        return False
        
    def set_i2c_address(self, addr):
        """
        Set the I2C communications to the slave specified by the address.
        The commands for I2C dev using the ioctl functions are specified in
        the i2c-dev.h file from i2c-tools.
        """
        I2C_SLAVE = 0x703
        fcntl.ioctl(self.file_read, I2C_SLAVE, addr)
        fcntl.ioctl(self.file_write, I2C_SLAVE, addr)
        self._address = addr

    def write(self, cmd):
        """Append the null character and send the string over I2C."""
        cmd += "\00"
        self.file_write.write(cmd.encode('latin-1'))

    def handle_raspi_glitch(self, response):
        """
        Change MSB to 0 for all received characters except the first.
        NOTE: This is a workaround for a Raspberry Pi I2C glitch.
        """
        return list(map(lambda x: chr(x & ~0x80), list(response)))

    def get_response(self, raw_data):
        """Filter null bytes from response."""
        return raw_data

    def response_valid(self, response):
        """Check if response is valid (starts with success code 1)."""
        valid = True
        error_code = None
        if len(response) > 0:
            error_code = str(response[0])
            if error_code != '1':
                valid = False
        return valid, error_code

    def get_device_info(self):
        """Return device info string."""
        if self._name == "":
            return f"{self._module} {self.address}"
        else:
            return f"{self._module} {self.address} {self._name}"
        
    def read(self, num_of_bytes=31):
        """Read a specified number of bytes from I2C, then parse and display the result."""
        raw_data = self.file_read.read(num_of_bytes)
        response = self.get_response(raw_data=raw_data)
        is_valid, error_code = self.response_valid(response=response)

        if is_valid:
            char_list = self.handle_raspi_glitch(response[1:])
            result = f"Success {self.get_device_info()}: {''.join(char_list)}"
        else:
            result = f"Error {self.get_device_info()}: {error_code}"

        return result

    def get_command_timeout(self, command):
        """Get appropriate timeout for command."""
        if command.upper().startswith(self.LONG_TIMEOUT_COMMANDS):
            return self._long_timeout
        elif not command.upper().startswith(self.SLEEP_COMMANDS):
            return self.short_timeout
        return None

    def query(self, command, max_retries=3):
        """
        Write a command to the board, wait the correct timeout,
        and read the response. Retries on 254 (still processing) responses.
        """
        self.write(command)
        current_timeout = self.get_command_timeout(command=command)
        if not current_timeout:
            return "sleep mode"
        
        # Initial wait
        time.sleep(current_timeout)
        
        # Read with retry logic for 254 (processing) responses
        for attempt in range(max_retries):
            result = self.read()
            
            # Check if response indicates still processing (254)
            if "Error" in result and ": 254" in result:
                # Wait additional time and retry
                retry_delay = 0.3 * (attempt + 1)  # Exponential backoff: 0.3s, 0.6s, 0.9s
                time.sleep(retry_delay)
                continue
            
            # Success or other error - return result
            return result
        
        # All retries exhausted
        return result

    def close(self):
        """Close file handles."""
        if self.file_read:
            try:
                self.file_read.close()
            except Exception:
                pass
        if self.file_write:
            try:
                self.file_write.close()
            except Exception:
                pass

    def list_i2c_devices(self):
        """Scan I2C bus for devices."""
        prev_addr = copy.deepcopy(self._address)
        i2c_devices = []
        for i in range(0, 128):
            try:
                self.set_i2c_address(i)
                self.read(1)
                i2c_devices.append(i)
            except IOError:
                pass
        # Restore the address we were using
        self.set_i2c_address(prev_addr)
        return i2c_devices

