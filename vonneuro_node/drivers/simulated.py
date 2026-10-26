"""A simulated RC device: the whole loop (hub, agent, driver, results, metering) with no hardware.

Copy this file to write a driver for your own hardware: declare devices and operations, refuse
what is unsafe in check(), and write result files into ctx.output_dir in run(). It also shows a
setting the node's admins change from the website (max_voltage_v), a default page, and VOLT:
the device accepts VOLT 0.1 programs through volt_device (one drive function).
"""
from __future__ import annotations

import json
import math
import time

from ..driver import DeviceSpec, Driver, Operation, RunContext, RunResult
from ..volt_device import VoltContract, run_volt_job, volt_check, volt_operation

R_OHM, C_FARAD = 1e3, 10e-6          # the simulated circuit: tau = R C = 10 ms

# What this device offers VOLT programs: drive.interval(u) puts u x the voltage limit on the RC.
VOLT = VoltContract(dialects=["dynamical"], input_range=(0.0, 1.0), duration_s=(0.001, 5.0),
                    drive="the RC driven at u x its voltage limit for the interval; current through it")


class SimulatedDriver(Driver):
    def __init__(self, slug: str = "sim-rc", tau_s: float = 0.01, max_voltage_v: float = 1.0):
        self.slug, self.tau_s, self.max_voltage_v = slug, tau_s, max_voltage_v
        self.default_max_v = max_voltage_v
        self.enabled = True

    def devices(self) -> list[DeviceSpec]:
        return [DeviceSpec(
            slug=self.slug, name="Simulated RC circuit", kind="rc", enabled=self.enabled,
            description="A first-order RC low-pass computed in software. Use it to try the network.",
            specs={"tau_s": self.tau_s, "max_voltage_v": self.max_voltage_v, "technology": "simulation"},
            operations=[Operation(
                name="step_response", summary="Charge the capacitor with a voltage step and sample it",
                params={
                    "amplitude": {"type": "number", "exclusiveMinimum": 0, "maximum": 5, "unit": "V", "default": 0.5},
                    "samples": {"type": "integer", "minimum": 2, "maximum": 4096, "default": 64},
                    "duration": {"type": "number", "exclusiveMinimum": 0, "maximum": 30, "unit": "s", "default": 0.05},
                },
                estimate="duration + 1", timeout_s=60, result="result.json", outputs=["result.json"]),
                volt_operation(VOLT)],
            # The node's admins change this from My hardware; check() enforces it.
            settings={"max_voltage_v": {"type": "number", "exclusiveMinimum": 0, "maximum": 5, "unit": "V",
                                        "default": self.default_max_v, "value": self.max_voltage_v,
                                        "description": "Largest step amplitude this device accepts"}},
            page=("# {{name}}\n\nA first-order RC circuit computed in software (R = 1 kΩ, C = 10 µF, τ = 10 ms): "
                  "the whole network loop with no hardware. Steps and VOLT programs may use up to "
                  "**{{setting:max_voltage_v}}**, a setting this node's admins change.\n\n"
                  "## From Python\n\n{{python}}\n\n## Operations\n\n{{operations}}\n\n## Settings\n\n{{settings}}\n"),
        )]

    def apply_settings(self, slug, values):
        if "max_voltage_v" in values:          # a real driver would also persist it (a file next to node.toml)
            self.max_voltage_v = values["max_voltage_v"] if values["max_voltage_v"] is not None else self.default_max_v

    def status(self, slug: str) -> dict:
        return {"temperature_c": 25.0}

    def check(self, slug, operation, params, ctx: RunContext):
        if operation == "volt_run":
            return volt_check(VOLT, params)
        if params["amplitude"] > self.max_voltage_v:
            return f"{slug} is limited to {self.max_voltage_v:g} V"
        return None

    def drive(self, u, seconds):
        """VOLT's drive.interval(u): the step response of the RC, sampled 240 times. Returns
        (current samples in A, energy delivered in J, details for the record)."""
        volts, n = u * self.max_voltage_v, 240
        time.sleep(min(seconds, 2.0))
        tau = R_OHM * C_FARAD
        current = [volts / R_OHM * math.exp(-seconds * (k + 0.5) / n / tau) for k in range(n)]
        energy = sum(volts * i * seconds / n for i in current)
        return current, energy, {"drive_v": volts, "simulated": True}

    def run(self, slug, operation, params, ctx: RunContext) -> RunResult:
        if operation == "volt_run":
            return run_volt_job(VOLT, params, ctx, self.drive)
        started = time.monotonic()
        n, duration, amplitude = params["samples"], params["duration"], params["amplitude"]
        deadline = started + duration
        while time.monotonic() < deadline:                 # the "measurement" takes `duration` seconds
            if ctx.cancelled():
                return RunResult(exit_code=-1, usage_seconds=time.monotonic() - started, logs="cancelled")
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        times = [duration * i / (n - 1) for i in range(n)]
        volts = [amplitude * (1 - math.exp(-t / self.tau_s)) for t in times]
        energy = 0.5 * 1e-6 * volts[-1] ** 2              # C = 1 uF: energy stored at the end
        (ctx.output_dir / "result.json").write_text(json.dumps({
            "device": slug, "operation": operation, "params": params,
            "time_s": times, "capacitor_v": volts, "energy_j": energy}), encoding="utf-8")
        return RunResult(exit_code=0, usage_seconds=time.monotonic() - started,
                         logs=f"step {amplitude:g} V, {n} samples over {duration:g} s", energy_j=energy)
