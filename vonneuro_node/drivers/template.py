"""A driver template: copy this file, replace the instrument calls, and your hardware is on the network.

It shows every part a driver can have:
  devices()        what the hardware is: devices, operations (typed, bounded parameters), settings, a page
  check()          what is unsafe: refuse it before anything is driven
  run()            drive the hardware, write result files into ctx.output_dir
  apply_settings() settings the node's admins change on the website (My hardware)
  status()         a cheap health report (never drives anything)
  VOLT             programs run through volt_device: you write one drive() function

Run it:  set  [driver] class = "my_driver:MyDriver"  in node.toml, then  vonneuro-node check  and  vonneuro-node run.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from vonneuro_node import DeviceSpec, Driver, Operation, RunContext, RunResult
from vonneuro_node.volt_device import VoltContract, run_volt_job, volt_check, volt_operation

SETTINGS_FILE = Path("my_driver_settings.json")     # next to node.toml: the admins' settings survive restarts
DEFAULTS = {"max_voltage_v": 1.0, "max_pulse_ms": 100.0}


class Instrument:
    """Stand-in for your own control code (pyvisa, pyserial, an SDK...). Replace these three methods."""

    def apply(self, volts: float, seconds: float, samples: int = 240) -> list[float]:
        """Hold the output at `volts` for `seconds`; return the measured current, evenly sampled (A)."""
        time.sleep(seconds)
        return [volts / 10e3] * samples

    def zero(self) -> None:
        """Leave the output at 0 V, whatever happened."""

    def healthy(self) -> bool:
        return True


class MyDriver(Driver):
    def __init__(self, port: str = "COM5"):          # [driver.options] in node.toml become these arguments
        self.instrument = Instrument()
        self.settings = {**DEFAULTS, **(json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {})}

    # What the device offers VOLT programs: drive.interval(u) holds u x the voltage limit.
    def volt_contract(self) -> VoltContract:
        return VoltContract(dialects=["dynamical", "adaptive"], input_range=(-1.0, 1.0),
                            duration_s=(0.005, self.settings["max_pulse_ms"] / 1e3),
                            drive=f"the device held at u x {self.settings['max_voltage_v']:g} V for the interval")

    def devices(self) -> list[DeviceSpec]:
        limit, longest = self.settings["max_voltage_v"], self.settings["max_pulse_ms"]
        return [DeviceSpec(
            slug="crossbar-8x8", name="8x8 memristor crossbar", kind="memristor",
            description="Pt/HfO2/Ti cells on a probe station; one cell at a time.",
            specs={"cells": 64, "max_voltage_v": limit, "technology": "HfO2"},
            operations=[
                Operation("pulse", "Hold one cell at a voltage for a time and measure its current",
                          {"row": {"type": "integer", "minimum": 0, "maximum": 7},
                           "col": {"type": "integer", "minimum": 0, "maximum": 7},
                           "volts": {"type": "number", "minimum": -limit, "maximum": limit, "unit": "V"},
                           "ms": {"type": "number", "minimum": 5, "maximum": longest, "unit": "ms", "default": 20}},
                          estimate="ms / 1000 + 1", timeout_s=60, result="pulse.json", outputs=["pulse.json"]),
                volt_operation(self.volt_contract()),
            ],
            settings={
                "max_voltage_v": {"type": "number", "exclusiveMinimum": 0, "maximum": 3.0, "unit": "V",
                                  "default": DEFAULTS["max_voltage_v"], "value": limit,
                                  "description": "Largest |voltage| on a cell"},
                "max_pulse_ms": {"type": "number", "minimum": 5, "maximum": 1000, "unit": "ms",
                                 "default": DEFAULTS["max_pulse_ms"], "value": longest,
                                 "description": "Longest pulse"},
            },
            page=("# {{name}}\n\nAn 8x8 crossbar of HfO2 memristors on our probe station. Pulses may be up to "
                  "**±{{setting:max_voltage_v}}** for **{{setting:max_pulse_ms}}**.\n\n"
                  "## From Python\n\n{{python}}\n\n## Operations\n\n{{operations}}\n\n## Settings\n\n{{settings}}\n"),
        )]

    def status(self, slug: str) -> dict:
        return {"instrument": "ok" if self.instrument.healthy() else "not responding"}

    def check(self, slug, operation, params, ctx: RunContext):
        if not self.instrument.healthy():
            return "The instrument is not responding"
        if operation == "volt_run":
            return volt_check(self.volt_contract(), params)
        if abs(params["volts"]) > self.settings["max_voltage_v"]:         # also bounded by the schema; checked again
            return f"Cells are limited to ±{self.settings['max_voltage_v']:g} V"
        return None

    def run(self, slug, operation, params, ctx: RunContext) -> RunResult:
        if operation == "volt_run":
            return run_volt_job(self.volt_contract(), params, ctx, self.drive)
        started = time.monotonic()
        try:
            current = self.instrument.apply(params["volts"], params["ms"] / 1e3)
        finally:
            self.instrument.zero()
        (ctx.output_dir / "pulse.json").write_text(json.dumps({**params, "current_a": current}))
        return RunResult(exit_code=0, usage_seconds=time.monotonic() - started,
                         logs=f"cell ({params['row']},{params['col']}) at {params['volts']:+g} V")

    def drive(self, u: float, seconds: float):
        """VOLT's drive.interval(u): (current samples, energy delivered, details for the record)."""
        volts = u * self.settings["max_voltage_v"]
        try:
            current = self.instrument.apply(volts, seconds)
        finally:
            self.instrument.zero()
        energy = sum(volts * i * seconds / len(current) for i in current)
        return current, energy, {"device_voltage_v": volts}

    def apply_settings(self, slug, values):
        for name, value in values.items():
            self.settings[name] = DEFAULTS[name] if value is None else value
        SETTINGS_FILE.write_text(json.dumps(self.settings))
