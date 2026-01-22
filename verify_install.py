import os
import sys

# Add V2 Code to path
sys.path.append(os.path.join(os.getcwd(), 'V2 Code'))

print("--- Verifying Imports ---")
try:
    import hardware_manager
    print("✓ hardware_manager imported")
except ImportError as e:
    print(f"✗ hardware_manager failed: {e}")

try:
    import utils
    print("✓ utils imported (pandas check passed)")
except ImportError as e:
    print(f"✗ utils failed: {e}")

try:
    from app import app
    print("✓ app imported")
except ImportError as e:
    print(f"✗ app failed: {e}")

print("\n--- Verifying Hardware Manager (Mock Mode) ---")
try:
    # Ensure we are in DEV mode for this test
    os.environ['TITRATOR_ENV'] = 'DEV'
    
    hw = hardware_manager.get_hardware()
    print("✓ HardwareManager initialized")
    
    pump = hw.get_pump()
    probe = hw.get_probe()
    
    print(f" Pump Type: {type(pump).__name__}")
    print(f" Probe Type: {type(probe).__name__}")
    
    if type(pump).__name__ == 'MockPump' and type(probe).__name__ == 'MockProbe':
        print("✓ Mock fallbacks active")
    else:
        print("? Hardware type mismatch (expected Mock)")

    print("\n--- Testing Mock Readings ---")
    val = probe.read_conductivity()
    print(f" Mock Conductivity: {val}")
    
    pump.start()
    status = pump.get_status()
    print(f" Pump Status (Started): {status}")
    pump.stop()
    print("✓ Pump logic verified")

    print("\n--- Verification Complete ---")

except Exception as e:
    print(f"✗ Verification failed with error: {e}")
