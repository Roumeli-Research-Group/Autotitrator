---
description: How to develop and deploy the Autotitrator project
---

# Autotitrator Development & Deployment Workflow

## Context
- **Development Machine**: This machine (where you are running) does NOT have the GPIO hardware connected. It is used for code editing, testing (without hardware), and pushing to GitHub.
- **Target Device**: A separate Raspberry Pi with all hardware connections (pump, conductivity probe, relay) where the application will actually run.

## Development Workflow

1. **Make code changes** on this development machine.
2. **Test without hardware**: Use mocking or skip hardware-dependent code during local testing.
3. **Commit and push** changes to GitHub:
   ```bash
   cd /home/NextX/.gemini/antigravity/scratch/auto_titrator
   git add .
   git commit -m "Your commit message"
   git push
   ```
4. **On the target device**: Pull changes and run the application:
   ```bash
   git pull
   cd "V2 Code"
   python3 app.py
   ```

## Key Files
- `V2 Code/app.py` - Main Flask web application
- `V2 Code/utils.py` - Pump control and titration logic (uses RPi.GPIO)
- `V2 Code/conductivity_probe.py` - Atlas Scientific I2C sensor driver

## Hardware Requirements (Target Device Only)
- Raspberry Pi with GPIO access
- Peristaltic pump connected to GPIO 23 via relay
- Atlas Scientific EZO-EC conductivity probe on I2C

## Important Notes
- Do NOT attempt to run `app.py` on the development machine — GPIO operations will fail.
- All GPIO-dependent testing must happen on the target device.
