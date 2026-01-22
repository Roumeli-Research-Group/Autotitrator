import sys
import os
import time
import glob
sys.path.append(os.getcwd())

print("--- 1. Testing Config ---")
from src.utils import get_setting
print(f"Flow Rate: {get_setting('DEFAULT_FLOW_RATE')}")

print("\n--- 2. Testing Engine & Persistence ---")
from src.titration.engine import TitrationEngine
from src.hardware import get_hardware

# Initialize hardware (Mock)
os.environ['TITRATOR_ENV'] = 'DEV'
hw = get_hardware() # force init

engine = TitrationEngine()
params = {'flow_rate': 100.0, 'step_volume': 10.0, 'wait_time': 0.05, 'readings_count': 1}

print("Running short titration...")
try:
    ts = engine.run_volumetric("VerifyRun", 20.0, "VerifyProject", params)
    print(f"Run Finished. Timestamp: {ts}")
except Exception as e:
    print(f"Run FAILED: {e}")
    sys.exit(1)

print("\n--- 3. Verifying Data File ---")
# Path should be src/static/titrations/VerifyProject/...
# Note: The engine logic uses absolute path based on __file__, so it should land in the right place regardless of CWD.
search_path = os.path.join("src", "static", "titrations", "VerifyProject", f"VerifyRun_volumetric_{ts}.csv")
if os.path.exists(search_path):
    print(f"SUCCESS: File found at {search_path}")
    with open(search_path, 'r') as f:
        lines = f.readlines()
        print(f"Line count: {len(lines)}")
        if len(lines) > 2:
            print("Tail:")
            print("".join(lines[-2:]))
else:
    print(f"FAILURE: File not found at {search_path}")
    # Search recursively just in case
    print("Listing ALL csvs:")
    for root, dirs, files in os.walk("src"):
        for file in files:
            if file.endswith(".csv"):
                 print(os.path.join(root, file))

print("\n--- 4. Testing Cleanup (Manual Check) ---")
# calling cleanup explicitly to verify no crash
hw.cleanup()
print("Cleanup called successfully.")
