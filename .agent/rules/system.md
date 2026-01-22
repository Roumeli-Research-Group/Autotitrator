---
trigger: always_on
---

This machine is for development only — no GPIO hardware.
The target device is a separate Raspberry Pi with all hardware connections.
Do NOT attempt to run app.py here — it will fail on GPIO.
Workflow: Edit → Push to GitHub → Pull on target device → Run there.