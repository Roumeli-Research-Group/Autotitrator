#!/usr/bin/env python3
"""
Gravimetric pump calibration: walks Stage 0 (bootstrap), Stage 1 (duration
series), Stage 2 (fit) and Stage 3 (verification), then writes the result
into config.json (and optionally a standalone JSON file).

    python3 pump_calibrate.py --simulate                    # dry run, no hardware
    python3 pump_calibrate.py --pin 23 --temp 24.5          # real

Model: V = Q * (t_on - tau). Slope of v_pulse vs t_on gives Q; the
x-intercept gives the per-actuation dead time tau.
"""

import argparse
import datetime
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.pump_control import fit_pulse_trials, water_density  # noqa: E402

MULTIPLIERS = [0.5, 0.75, 1.5, 3, 6, 12]
STAGE0_RUNS = [5, 10, 15]

# Simulated pump characteristics (used to auto-generate masses on blank input)
SIM_Q = 0.27      # mL/s
SIM_TAU = 0.042   # s


class Pump:
    def __init__(self, pin, simulate, time_scale=1.0):
        self.simulate = simulate
        self.time_scale = time_scale
        if not simulate:
            import RPi.GPIO as GPIO
            self.GPIO = GPIO
            self.pin = pin
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.HIGH)  # relay off (active LOW)

    def pulse_train(self, on_time, pulses, gap):
        for i in range(pulses):
            if not self.simulate:
                self.GPIO.output(self.pin, self.GPIO.LOW)
            time.sleep(on_time * self.time_scale)
            if not self.simulate:
                self.GPIO.output(self.pin, self.GPIO.HIGH)
            if i < pulses - 1:
                time.sleep(gap * self.time_scale)
            done = i + 1
            print(f"\r  pulse {done}/{pulses}", end="", flush=True)
        print()

    def cleanup(self):
        if not self.simulate:
            self.GPIO.cleanup()


def ask_mass(prompt, simulate, expected=None):
    """Prompt for a balance reading. In --simulate, blank input generates a
    plausible mass from the simulated pump so the full flow can be tested."""
    while True:
        raw = input(f"{prompt} [g]: ").strip()
        if raw == "" and simulate and expected is not None:
            noisy = expected * (1 + random.gauss(0, 0.001))
            print(f"  (simulated: {noisy:.3f} g)")
            return noisy
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            pass
        print("  Enter a positive number" +
              (" (or blank to simulate)" if simulate else ""))


def main():
    ap = argparse.ArgumentParser(description="Gravimetric pump calibration")
    ap.add_argument('--simulate', action='store_true', help='no hardware; sleeps compressed')
    ap.add_argument('--pin', type=int, default=23, help='relay BCM pin (default 23)')
    ap.add_argument('--temp', type=float, default=None, help='water temperature degC')
    ap.add_argument('--step-volume', type=float, default=0.1, help='step volume mL (default 0.1)')
    ap.add_argument('--gap', type=float, default=0.3, help='inter-pulse gap s (default 0.3)')
    ap.add_argument('--reps', type=int, default=3, help='replicates per on-time (default 3)')
    ap.add_argument('--target-mass', type=float, default=5.0, help='target g per trial (default 5)')
    ap.add_argument('--bootstrap-seconds', type=float, default=30.0,
                    help='total Stage 0 pumping time (default 30, split 5/10/15-style)')
    ap.add_argument('--verify-pulses', type=int, default=100, help='Stage 3 pulses (default 100)')
    ap.add_argument('--out', type=str, default=None, help='also write results to this JSON file')
    args = ap.parse_args()

    if args.temp is None:
        args.temp = float(input("Water temperature [degC]: ").strip() or "25.0")
    rho = water_density(args.temp)
    print(f"\nrho = {rho:.5f} g/mL at {args.temp:.1f} degC")
    print(f"Step volume {args.step_volume} mL, gap {args.gap} s, "
          f"{args.reps} reps, mode = {'SIMULATE' if args.simulate else f'GPIO pin {args.pin}'}\n")

    time_scale = 0.02 if args.simulate else 1.0
    pump = Pump(args.pin, args.simulate, time_scale)

    def sim_mass(on_time, pulses):
        return rho * pulses * SIM_Q * max(0.0, on_time - SIM_TAU)

    try:
        # ---- Stage 0: bootstrap -----------------------------------------
        print("=== Stage 0 - Bootstrap ===")
        print("Prime the line until bubble-free. Tare the beaker before each run.")
        scale = args.bootstrap_seconds / sum(STAGE0_RUNS)
        runs = [round(r * scale, 1) for r in STAGE0_RUNS]
        total_mass, total_time = 0.0, 0.0
        for dur in runs:
            input(f"\nPlace tared beaker; ENTER to run pump {dur} s continuously...")
            pump.pulse_train(dur, 1, 0)
            m = ask_mass(f"Mass for the {dur}s run", args.simulate, sim_mass(dur, 1))
            total_mass += m
            total_time += dur
        q_rough = total_mass / (rho * total_time)
        print(f"\nQ_rough = {total_mass:.3f} / ({rho:.5f} x {total_time:.0f}) "
              f"= {q_rough:.4f} mL/s")

        # ---- Stage 1: duration series -----------------------------------
        print("\n=== Stage 1 - Duration series ===")
        t_op = args.step_volume / q_rough
        print(f"Operating point t_op = {args.step_volume} / {q_rough:.4f} = {t_op:.3f} s")
        sheet = []
        for mult in MULTIPLIERS:
            t = max(0.05, min(30.0, round(t_op * mult, 3)))
            n = max(3, min(200, round(args.target_mass / (rho * q_rough * t * 0.85))))
            sheet.append((t, n))
        print("\n  on-time (s) |  N  | ~collected | pumping time")
        for t, n in sheet:
            est = rho * q_rough * t * n * 0.85
            print(f"  {t:11.3f} | {n:3d} | {est:7.1f} g  | {n * (t + args.gap):5.0f} s")

        trials = []
        for t, n in sheet:
            for rep in range(args.reps):
                input(f"\n[{t:.3f}s x {n}] rep {rep + 1}/{args.reps}: "
                      f"tared beaker in place, ENTER to fire...")
                pump.pulse_train(t, n, args.gap)
                m = ask_mass("Total mass", args.simulate, sim_mass(t, n))
                trials.append([t, n, m])

        # ---- Stage 2: fit -------------------------------------------------
        print("\n=== Stage 2 - Fit ===")
        fit = fit_pulse_trials(trials, args.temp)
        print(f"  Q   = {fit['Q']:.4f} mL/s")
        print(f"  tau = {fit['tau_ms']:.1f} ms")
        print(f"  R^2 = {fit['r2']:.5f}   residual RMS = {fit['resid_rms_ul']:.2f} uL")
        for w in fit['warnings']:
            print(f"  WARNING: {w}")

        tau_use = max(0.0, fit['tau'])

        # ---- Stage 3: verification ---------------------------------------
        print("\n=== Stage 3 - Verification ===")
        t_step = args.step_volume / fit['Q'] + tau_use
        target = args.verify_pulses * args.step_volume * rho
        print(f"t_step = {args.step_volume} / {fit['Q']:.4f} + {tau_use:.4f} = {t_step:.4f} s")
        print(f"Firing {args.verify_pulses} pulses; target mass = {target:.3f} g "
              f"(pass within 0.5%)")
        input("Tared beaker in place, ENTER to fire...")
        pump.pulse_train(t_step, args.verify_pulses, args.gap)
        m = ask_mass("Verification mass", args.simulate,
                     rho * args.verify_pulses * SIM_Q * max(0.0, t_step - SIM_TAU))
        err_pct = (m - target) / target * 100.0
        verdict = "PASS" if abs(err_pct) <= 0.5 else "FAIL"
        print(f"  {m:.3f} g vs {target:.3f} g -> {err_pct:+.2f}%  [{verdict}]")
        if verdict == "FAIL":
            print("  If a short step fails while long ones pass (or vice versa), the "
                  "two-constant model is not describing this pump: run the burst-length "
                  "and gap checks from the calibration procedure.")

        # ---- Store --------------------------------------------------------
        result = {
            "PUMP_FLOW_RATE_MLS": round(fit['Q'], 4),
            "PUMP_DEAD_TIME_S": round(tau_use, 4),
            "PUMP_CAL_STEP_VOLUME_ML": args.step_volume,
            "PUMP_CAL_ON_TIME_S": round(t_step, 4),
            "PUMP_CAL_DATE": datetime.date.today().isoformat(),
            "PUMP_CAL_TEMP_C": args.temp,
            "PUMP_CAL_GAP_S": args.gap,
            "PUMP_CAL_R2": round(fit['r2'], 5),
            "PUMP_CAL_RESID_UL": round(fit['resid_rms_ul'], 2),
            "PUMP_CAL_RAW": trials,
        }
        print("\n" + json.dumps(result, indent=2))

        if input("\nWrite into config.json so the app uses it? [y/N]: ").strip().lower() == 'y':
            from src.utils import update_setting
            for k, v in result.items():
                update_setting(k, v)
            update_setting("DEFAULT_FLOW_RATE", result["PUMP_FLOW_RATE_MLS"])
            update_setting("LAST_CALIBRATION_DATE", result["PUMP_CAL_DATE"])
            print("config.json updated.")
        if args.out:
            with open(args.out, 'w') as f:
                json.dump(result, f, indent=2)
            print(f"Wrote {args.out}")

    finally:
        pump.cleanup()


if __name__ == '__main__':
    main()
