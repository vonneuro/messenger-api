"""VOLT on your device: accept VOLT 0.1 `system` programs as an operation, as the vonNeuro bench does.

A VOLT program states what to do to the matter (`evolve <state> for T using drive.interval(u)`),
what to observe (`<state>.terminal_current`, as `real` or `vector`), and contracts the device must
honour. Your driver declares the device's **contract** and implements **one drive**; this module
compiles the program (volt.py, the same compiler as the bench), checks the contracts, and builds
the reply. A program the device cannot honour is refused before anything is driven.

    from vonneuro_node.volt_device import VoltContract, volt_operation, volt_check, run_volt_job

    CONTRACT = VoltContract(dialects=["dynamical"], input_range=(0.0, 1.0), duration_s=(0.005, 2.0),
                            drive="W1 at u x 5 V for the interval")

    class MyDriver(Driver):
        def devices(self):
            return [DeviceSpec(..., operations=[volt_operation(CONTRACT)])]
        def check(self, slug, operation, params, ctx):
            return volt_check(CONTRACT, params) if operation == "volt_run" else None
        def run(self, slug, operation, params, ctx):
            return run_volt_job(CONTRACT, params, ctx, self.drive)
        def drive(self, u, seconds):
            ...                                 # drive the hardware at u for seconds, measure the current
            return samples_a, energy_j, {"drive_v": u * 5}

People then run programs with ``client.device("you/device").run("volt_run", source=..., inputs={"u": 0.5})``.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from dataclasses import dataclass, field

from .driver import Operation, RunContext, RunResult
from .volt import VoltError, compile_source

UNITS = {"us": 1e-6, "ms": 1e-3, "s": 1.0}
WINDOWS = 24            # a `vector` observation: the mean of each of 24 equal windows
MAX_SOURCE_BYTES = 32768


class VoltRefusal(ValueError):
    """The program cannot run on this device; nothing was driven. The message names the clause."""


@dataclass
class VoltContract:
    """What the device offers to VOLT programs (the bench's are in vn_hal.volt_bench.DEVICES)."""
    dialects: list[str]                                  # e.g. ["dynamical"], ["dynamical", "adaptive"]
    input_range: tuple[float, float] = (-1.0, 1.0)       # allowed u
    duration_s: tuple[float, float] = (0.005, 0.5)       # allowed evolve interval
    capabilities: list[str] = field(default_factory=lambda: ["drive.interval", "observe.terminals"])
    observables: list[str] = field(default_factory=lambda: ["terminal_current"])
    drive: str = ""                                      # what drive.interval(u) does, in words
    unit: str = "A"                                      # unit of the observed samples


def volt_operation(contract: VoltContract, summary: str | None = None) -> Operation:
    """The `volt_run` operation for a device with this contract."""
    return Operation(
        "volt_run", summary or f"Run a VOLT 0.1 system program ({contract.drive or 'drive.interval(u)'})",
        {"source": {"type": "string", "minLength": 1, "maxLength": MAX_SOURCE_BYTES,
                    "description": "VOLT 0.1 system program"},
         "inputs": {"type": "object", "additionalProperties": {"type": "number"}, "maxProperties": 16, "default": {}}},
        timeout_s=max(60.0, contract.duration_s[1] + 30), result="result.json", outputs=["result.json"])


def lower(source: str, inputs: dict, contract: VoltContract) -> dict:
    """Compile the program and check it against the contract. Pure: drives nothing."""
    if len(source.encode()) > MAX_SOURCE_BYTES:
        raise VoltRefusal(f"source exceeds {MAX_SOURCE_BYTES} bytes")
    try:
        vng = compile_source(source)
    except VoltError as error:
        raise VoltRefusal(str(error)) from error
    contracts, checked, unenforced = vng["contracts"], [], []
    wanted = contracts["device"].get("required_capabilities", [])
    wanted = wanted if isinstance(wanted, list) else [wanted]
    missing = [c for c in wanted if c not in contract.capabilities]
    if missing:
        raise VoltRefusal(f"device.required_capabilities: {', '.join(map(str, missing))} not offered "
                          f"(offers {', '.join(contract.capabilities)})")
    checked.append("device.required_capabilities")
    dialects = contracts["device"].get("dialects", [])
    dialects = dialects if isinstance(dialects, list) else [dialects]
    foreign = [d for d in dialects if d not in contract.dialects]
    if foreign:
        raise VoltRefusal(f"device.dialects: {', '.join(map(str, foreign))} not a dialect of this device "
                          f"({', '.join(contract.dialects)})")
    checked.append("device.dialects")
    unenforced += [f"device.{k}" for k in contracts["device"] if k not in ("required_capabilities", "dialects")]
    operation = next(n for n in vng["nodes"] if n["kind"] == "operation")
    if operation["action"] != "drive.interval" or "drive.interval" not in contract.capabilities:
        raise VoltRefusal(f"evolve action {operation['action']!r}: this device offers drive.interval only")
    if len(operation["inputs"]) != 1:
        raise VoltRefusal("drive.interval takes exactly one input u")
    name = operation["inputs"][0]
    value = inputs.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise VoltRefusal(f"input {name!r} must be a finite number")
    low, high = contract.input_range
    if not low <= value <= high:
        raise VoltRefusal(f"input {name!r} = {value} outside {float(low)}..{float(high)}")
    unknown = set(inputs) - {n["name"] for n in vng["nodes"] if n["kind"] == "input"}
    if unknown:
        raise VoltRefusal(f"unknown inputs: {', '.join(sorted(unknown))}")
    duration = operation["duration"]
    if duration["unit"] not in UNITS:
        raise VoltRefusal(f"evolve duration unit {duration['unit']!r}: use us, ms or s")
    seconds = duration["value"] * UNITS[duration["unit"]]
    shortest, longest = contract.duration_s
    if not shortest <= seconds <= longest:
        raise VoltRefusal(f"evolve duration {seconds*1e3:g} ms outside {shortest*1e3:g}..{longest*1e3:g} ms")
    observations = []
    for node in vng["nodes"]:
        if node["kind"] != "observation":
            continue
        state, _, observable = node["selector"].partition(".")
        if state != operation["state"] or observable not in contract.observables:
            raise VoltRefusal(f"observe {node['name']}: {node['selector']!r} is not observable here "
                              f"(use {operation['state']}.{' or '.join(contract.observables)})")
        if node["type"] not in ("real", "vector"):
            raise VoltRefusal(f"observe {node['name']}: type {node['type']!r}; use real or vector")
        observations.append({"name": node["name"], "type": node["type"]})
    returned = next(n["value"] for n in vng["nodes"] if n["kind"] == "result")
    execution = contracts["execution"]
    checked.append("execution.intent")
    if "output" in execution:
        if execution["output"] != returned:
            raise VoltRefusal(f"execution.output {execution['output']!r} is not the returned observation {returned!r}")
        checked.append("execution.output")
    unenforced += [f"execution.{k}" for k in execution if k not in ("intent", "output")]
    unenforced += [f"thermodynamics.{k}" for k in contracts["thermodynamics"]]
    return {"vng": vng, "vng_sha256": hashlib.sha256(json.dumps(vng, sort_keys=True).encode()).hexdigest(),
            "input": {"name": name, "value": float(value)}, "duration_s": seconds, "observations": observations,
            "returns": returned, "contracts_checked": checked, "contracts_not_enforced": unenforced}


def volt_check(contract: VoltContract, params: dict) -> str | None:
    """For Driver.check: why this program cannot run here, or None."""
    try:
        lower(params.get("source", ""), params.get("inputs") or {}, contract)
    except VoltRefusal as refusal:
        return f"VOLT: {refusal}"
    return None


def _windows(values: list[float]) -> list[float]:
    n = len(values)
    return [statistics.fmean(values[k * n // WINDOWS:max((k + 1) * n // WINDOWS, k * n // WINDOWS + 1)])
            for k in range(WINDOWS)]


def record(plan: dict, contract: VoltContract, samples: list[float], energy_j: float | None, details: dict) -> dict:
    """The reply of one run, in the bench's format (result, observations, energy, contracts, provenance)."""
    values = {o["name"]: (statistics.fmean(samples) if o["type"] == "real" else _windows(samples))
              for o in plan["observations"]}
    return {"ok": True, "execution": "hardware", "language": plan["vng"]["language"], "program": plan["vng"]["program"],
            "inputs": {plan["input"]["name"]: plan["input"]["value"]},
            "result": {"name": plan["returns"], "value": values[plan["returns"]], "unit": contract.unit},
            "observations": values, "interval_s": plan["duration_s"], "measured_energy_j": energy_j,
            "contracts_checked": plan["contracts_checked"], "contracts_not_enforced": plan["contracts_not_enforced"],
            "thermodynamics_declared": plan["vng"]["contracts"]["thermodynamics"],
            "provenance": {**plan["vng"]["provenance"], "vng_sha256": plan["vng_sha256"], "lowering": contract.drive, **details},
            "vng": plan["vng"]}


def run_volt_job(contract: VoltContract, params: dict, ctx: RunContext, drive) -> RunResult:
    """For Driver.run: lower, call ``drive(u, seconds) -> (samples, energy_j, details)``, write result.json."""
    started = time.monotonic()
    try:
        plan = lower(params["source"], params.get("inputs") or {}, contract)
    except VoltRefusal as refusal:
        (ctx.output_dir / "result.json").write_text(json.dumps({"ok": False, "execution": "none", "error": str(refusal)}))
        return RunResult(exit_code=1, usage_seconds=time.monotonic() - started, logs=f"VOLT refused: {refusal}")
    samples, energy_j, details = drive(plan["input"]["value"], plan["duration_s"])
    if not samples:
        raise RuntimeError("the drive returned no samples")
    reply = record(plan, contract, list(map(float, samples)), energy_j, details or {})
    (ctx.output_dir / "result.json").write_text(json.dumps(reply, allow_nan=False))
    value = reply["result"]["value"]
    shown = f"{value:+.4g} {contract.unit}" if isinstance(value, float) else f"{len(value)} values"
    return RunResult(exit_code=0, usage_seconds=time.monotonic() - started, energy_j=energy_j,
                     logs=f"{reply['program']}: {reply['result']['name']} = {shown}")
