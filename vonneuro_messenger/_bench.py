"""Bench helpers for the synchronous client: hardware list, scope/wavegen measurements,
live memristor sessions, the Nano (MUX + pIsing sampler) and account usage.

Every hardware call goes through the vonNeuro Messenger server, which enforces the
operator's hardware switches and voltage limits and queues each job for the shared
instrument (first come, first served).
"""
from __future__ import annotations

import itertools
import json
import time
import uuid
from typing import Any, Optional

from ._models import JobStatus

_request_ids = itertools.count(int(time.time() * 1000) * 1000)  # unique, increasing, < 2**53

# Pulses (MessengerClient.pulse): one VOLT drive.interval(u) per pulse, with u = volts / full scale.
# Memristors: the device itself is held at u * 0.3 V (W2 around the 2.47 V bias), so -0.3..+0.3 V.
# RC coprocessors: W1 drives u * the channel's drive voltage (u >= 0: polarised capacitors); the
# drive per channel is the bench's MUX-RC plan, 4 V except channel 4 (2.24 V, its collector swing).
MEMRISTOR_PULSE_V = 0.3
RC_DRIVE_V = (4.0, 4.0, 4.0, 4.0, 2.24, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0)
PULSE_MS = {"rc": (5.0, 2000.0), "memristor": (5.0, 500.0)}


def pulse_program(kind: str, ms: float, observe: str = "vector") -> str:
    """The VOLT 0.1 program of one pulse: hold the drive for `ms` and observe the terminal current,
    as 24 window means ("vector") or its mean ("real"). The same program the script lab sends."""
    state, dialects = ("capacitor", "dynamical") if kind == "rc" else ("material", "dynamical, adaptive")
    return (f"system {kind}_pulse(u: real) {{\n"
            f"  state {state}: {'charge' if kind == 'rc' else 'adaptive'};\n"
            f"  evolve {state} for {ms:g} ms using drive.interval(u);\n"
            f"  observe current: {observe} from {state}.terminal_current;\n"
            "  return current;\n"
            "  execution { intent = evolve; output = current; }\n"
            f"  device {{ dialects = [{dialects}]; required_capabilities = [drive.interval, observe.terminals]; }}\n"
            '  thermodynamics { process_boundary = "drive_to_observation"; }\n'
            "}")


class LiveSession:
    """A claimed live session on a bench fixture (e.g. memristor device 22).

    The memristor live fixtures predate the MUX2 array and are refused while it is wired.
    Use as a context manager so the fixture is always released::

        with client.live("memristor22") as session:
            reply = session.step(0.5)        # one measured burst, input in [-1, 1]
            print(reply["memristor"]["reservoir_features_v"])
    """

    def __init__(self, client, fixture: str, pins: list[int]):
        self.client, self.fixture, self.pins = client, fixture, list(pins)
        self.owner = "python-" + uuid.uuid4().hex[:12]
        self.token: Optional[str] = None

    def _call(self, action: str, **fields) -> dict:
        body = {"fixture": self.fixture, "action": action, "request_id": next(_request_ids),
                "owner": self.owner, "pins": self.pins, **fields}
        if self.token:
            body["token"] = self.token
        return self.client._request("POST", "/api/game/live", json=body).json()

    def status(self) -> dict:
        """Bench identity and whether it is occupied; never drives hardware."""
        return self._call("status")

    def claim(self) -> "LiveSession":
        reply = self._call("claim")
        if not reply.get("ok"):
            from ._client import MessengerError
            raise MessengerError(409, reply.get("error", "Fixture unavailable"))
        self.token = reply["token"]
        return self

    def step(self, feature: float, *, retries: int = 10) -> dict:
        """Run one measurement with input ``feature`` in [-1, 1] and return the bench reply.

        If another job holds the shared instrument the bench answers ``retry``; this waits
        briefly and tries again (up to ``retries`` times).
        """
        from ._client import MessengerError
        for _ in range(retries + 1):
            reply = self._call("step", mem_feature=float(feature), rc_feature=0.0, reservoir=True)
            if reply.get("ok"):
                return reply
            if not reply.get("retry"):
                raise MessengerError(409, reply.get("error", "Measurement failed"))
            time.sleep(.2)
        raise MessengerError(409, "Instrument stayed busy; try again later")

    def release(self) -> None:
        if self.token:
            try:
                self._call("release")
            finally:
                self.token = None

    def __enter__(self):
        return self.claim()

    def __exit__(self, *exc):
        self.release()


class BenchMixin:
    # ── Account and hardware ────────────────────────────────────────────────

    def usage(self) -> dict:
        """Compute time remaining, and bench time used by this account."""
        return self._request("GET", "/api/billing/usage").json()

    def request_time(self, hours: float, reason: str) -> dict:
        """Ask an admin for more compute time (up to 24 h per request, one open request at a time).
        The time is added to your account when an admin approves; follow it with time_requests()."""
        return self._request("POST", "/api/billing/time-requests", json={"hours": hours, "reason": reason}).json()

    def time_requests(self) -> list[dict]:
        """Your requests for more time, newest first, with their status and the hours granted."""
        return self._request("GET", "/api/billing/time-requests").json()

    def hardware(self) -> list[dict]:
        """Operator-declared bench devices: id, name, kind, enabled, scope/wavegen channel,
        device, pins, max_voltage_v. Never touches the instruments."""
        return self._request("GET", "/api/hardware/available").json()["items"]

    def instrument_status(self) -> dict:
        """Scope/wavegen serial, whether driving is enabled and each output's worker limits."""
        return self._request("GET", "/api/jobs/instrument-status").json()

    def set_hardware(self, item_id: str, enabled: bool) -> list[dict]:
        """Admin only: switch a device on or off for every non-admin account."""
        return self._request("POST", f"/api/admin/hardware/{item_id}/enabled", json={"enabled": enabled}).json()["items"]

    # ── Scope / wavegen measurement jobs ────────────────────────────────────

    def measure(self, *, channel: Optional[int] = None, waveform: str = "sine", amplitude: float = 0.1,
                offset: float = 0.0, frequency: float = 1000.0, duration: float = 0.004,
                samples: int = 1024, sample_rate: float = 100_000.0, range_v: Optional[float] = None,
                fixture: Optional[str] = None, name: str = "python measurement", wait_timeout: float = 180.0) -> dict:
        """Run one finite scope/wavegen job and return its ``measurements.json``.

        ``channel=None`` captures both scope inputs without driving anything. ``channel=1/2``
        drives W1/W2 with ``waveform`` (dc, sine, square, triangle) for ``duration`` seconds;
        the scope window (samples / sample_rate) must cover the whole output.

        Both outputs feed a multiplexer, so neither may go negative: the drive must stay within
        ``offset - amplitude >= 0``. W1 (the RC board) is also capped at the hardware limit (2.24 V);
        W2 (the MUX2 memristor array) must stay within 0..5 V. To drive a memristor use
        :meth:`memristor_array`, which swings W2 around the array's 2.47 V bias so the device sees
        both polarities. A ``fixture`` preset ("rc_led", "memristor_array") replaces all drive
        settings; "device22"/"device23" are the retired direct-wired fixtures.
        """
        from ._client import MessengerError
        args = ["--samples", str(samples), "--sample-rate", str(sample_rate)]
        if range_v is not None:
            args += ["--range", str(range_v)]
        if fixture:
            args += ["--fixture", fixture]
        elif channel is not None:
            args += ["--channel", str(channel), "--waveform", waveform, "--amplitude", str(amplitude),
                     "--offset", str(offset), "--frequency", str(frequency), "--duration", str(duration)]
        job = self._request("POST", "/api/jobs", json={"name": name, "job_type": "messenger_instrument",
                                                        "payload": {"args": args}}).json()
        job = self.wait(job["id"], poll_interval=0.5, timeout=wait_timeout)
        if job.status != JobStatus.DONE:
            raise MessengerError(500, f"Job {job.id} {job.status}: {(job.logs or '')[-400:]}")
        data = json.loads(self._request("GET", f"/api/jobs/{job.id}/outputs/measurements.json").content)
        data["messenger_job"] = {"id": job.id, "status": str(job.status)}
        return data

    def memristor_array(self, channel: int, amplitude: float = 0.25, frequency: float = 10.0,
                        wait_timeout: Optional[float] = None) -> dict:
        """Guarded I-V of one memristor on the MUX2 array (channels 0-7 device 23, 8-15 device 22).

        Each memristor runs from the mux (W2) through a 10 kOhm sense resistor to end B, held at
        about 2.47 V. W2 only ever takes voltages between 0 and the mux supply, yet the device sees
        both polarities: device voltage = W2 - end B - sense drop, so W2 above the bias pushes
        current in and W2 below it pulls current out. The bench holds W2 at the bias around the
        selection (the device at 0 V), checks the rest current, then plays W2 = bias +
        ``amplitude`` * sin(2 pi f t), so +-``amplitude`` volts (at most 1 V) across the device and
        its resistor: 6 cycles at ``frequency`` Hz, cycles 3-5 averaged. Any frequency above 0 up to 100 Hz: a slow sweep lasts 6/frequency seconds
        (60 s at 0.1 Hz) and is refused if that is more than your bench time left. Returns
        ``measurements.json``; the curve is under ``memristor``."""
        from ._client import MessengerError
        if wait_timeout is None:
            wait_timeout = max(180.0, 6.0 / frequency + 120.0)
        args = ["--fixture", "memristor_array", "--array-channel", str(channel),
                "--amplitude", str(amplitude), "--frequency", str(frequency)]
        job = self._request("POST", "/api/jobs", json={"name": f"memristor array C{channel:02d}",
                                                        "job_type": "messenger_instrument",
                                                        "payload": {"args": args}}).json()
        job = self.wait(job["id"], poll_interval=0.5, timeout=wait_timeout)
        if job.status != JobStatus.DONE:
            raise MessengerError(500, f"Job {job.id} {job.status}: {(job.logs or '')[-400:]}")
        data = json.loads(self._request("GET", f"/api/jobs/{job.id}/outputs/measurements.json").content)
        data["messenger_job"] = {"id": job.id, "status": str(job.status)}
        return data

    def volt_run(self, source: str, inputs: dict, target: str, wait_timeout: float = 180.0) -> dict:
        """Run a VOLT 0.1 ``system`` program on one bench device and return its record.

        ``target`` is ``"memristor:N"`` or ``"rc:N"`` (N in 0-15). The server compiles the source,
        checks its ``device`` contract against that device, lowers ``evolve ... using
        drive.interval(u)`` to a guarded drive, and returns the observation, the measured energy
        at the terminals, which contract clauses were checked or are not yet enforced, and the VNG.
        A program the device cannot honour is refused before anything is driven: the reply then has
        ``ok: False`` and the failing clause in ``error``."""
        from ._client import MessengerError
        job = self._request("POST", "/api/jobs", json={"name": f"VOLT on {target}", "job_type": "volt_run",
                                                        "payload": {"source": source, "inputs": inputs,
                                                                    "target": target}}).json()
        job = self.wait(job["id"], poll_interval=0.5, timeout=wait_timeout)
        try:
            data = json.loads(self._request("GET", f"/api/jobs/{job.id}/outputs/result.json").content)
        except MessengerError:
            raise MessengerError(500, f"Job {job.id} {job.status}: {(job.logs or '')[-400:]}")
        data["messenger_job"] = {"id": job.id, "status": str(job.status)}
        return data

    # ── Pulses: a voltage on one channel for a set time ─────────────────────

    def pulse(self, target: str, volts: float, ms: float, *, observe: str = "vector",
              wait_timeout: float = 180.0) -> dict:
        """Apply ``volts`` to one channel for ``ms`` milliseconds and measure its current.

        ``target`` is ``"rc:N"`` or ``"memristor:N"`` (N in 0-15).

        * ``"memristor:N"``: the memristor itself is held at ``volts`` (-0.3 to +0.3 V) for 5-500 ms,
          after a rest check at 0 V; current through its 10 kOhm sense resistor.
        * ``"rc:N"``: W1 drives the RC branch at ``volts`` (0 V up to the channel's drive voltage,
          ``RC_DRIVE_V[N]``: 4 V, 2.24 V on channel 4) for 5-2000 ms; current through the 100 Ohm sense.

        Returns the ``volt_run`` record: ``record["result"]["value"]`` is the current in A, as 24
        window means across the pulse (``observe="vector"``) or one mean (``observe="real"``);
        ``record["measured_energy_j"]`` is the work done at the device, and ``record["pulse"]``
        repeats what was asked and what the bench applied. Out-of-range requests raise
        ``MessengerError`` before anything is queued."""
        from ._client import MessengerError
        kind, _, number = str(target).partition(":")
        if kind not in PULSE_MS or not number.isdigit() or not 0 <= int(number) <= 15:
            raise MessengerError(422, "target must be 'rc:N' or 'memristor:N' with N in 0..15")
        channel = int(number)
        full = RC_DRIVE_V[channel] if kind == "rc" else MEMRISTOR_PULSE_V
        low = 0.0 if kind == "rc" else -full
        if not low <= volts <= full:
            raise MessengerError(422, f"{target}: volts must be in {low:g}..{full:g} V"
                                      + (" (polarised capacitors: nonnegative)" if kind == "rc" else " across the memristor"))
        shortest, longest = PULSE_MS[kind]
        if not shortest <= ms <= longest:
            raise MessengerError(422, f"{target}: ms must be in {shortest:g}..{longest:g}")
        if observe not in ("vector", "real"):
            raise MessengerError(422, "observe must be 'vector' (24 window means) or 'real' (the mean)")
        u = round(volts / full, 9)
        reply = self.volt_run(pulse_program(kind, ms, observe), {"u": u}, target, wait_timeout=wait_timeout)
        if not reply.get("ok", True):
            raise MessengerError(409, f"{target}: {reply.get('error')}")
        provenance = reply.get("provenance") or {}
        reply["pulse"] = {"target": target, "volts": volts, "ms": ms, "u": u,
                          "applied_v": provenance.get("device_voltage_v", provenance.get("drive_v"))}
        return reply

    def pulse_rc(self, channel: int, volts: float, ms: float, **options) -> dict:
        """RC coprocessor ``channel`` at ``volts`` (0 up to ``RC_DRIVE_V[channel]``) for ``ms`` (5-2000 ms)."""
        return self.pulse(f"rc:{channel}", volts, ms, **options)

    def pulse_memristor(self, channel: int, volts: float, ms: float, **options) -> dict:
        """Memristor ``channel`` held at ``volts`` (-0.3 to +0.3 V) for ``ms`` (5-500 ms)."""
        return self.pulse(f"memristor:{channel}", volts, ms, **options)

    # ── Live fixtures ───────────────────────────────────────────────────────

    def live(self, fixture: str = "memristor22", pins: Optional[list[int]] = None) -> LiveSession:
        """A live session on a fixture; use ``with client.live(...) as session``.
        Device 22's pins default to the ones in the hardware list.

        The memristor fixtures ("memristor22", "memristor", "dual_reservoir") belong to the direct
        wiring and are refused while the MUX2 array is wired; use :meth:`memristor_array` or
        :meth:`volt_run` with ``"memristor:N"`` instead."""
        if pins is None:
            pins = next((item["pins"] for item in self.hardware()
                         if item.get("id") == "memristor-d22" and item.get("pins")), [2, 10]) \
                if fixture == "memristor22" else [6, 14]
        return LiveSession(self, fixture, pins)

    # ── Nano: MUX routing and the pIsing p-bit sampler ──────────────────────

    def nano_status(self) -> dict:
        return self._request("GET", "/api/nano/status").json()

    def nano(self, action: str, **data: Any) -> dict:
        """One Nano command: connect (mode="mock"|"serial", port), load (J, h, temperature[, seed]),
        select (channel 0-15), off, adc, disconnect, cancel. Returns the Nano status.

        ``select`` is refused on the real Nano while the memristor array is wired (the two muxes
        share their address lines); channels are selected only by the guarded bench paths."""
        return self._request("POST", "/api/nano/command", json={"action": action, "data": data}).json()

    def nano_sample(self, burn_in: int = 0, thin: int = 10, wait_timeout: float = 90.0) -> dict:
        """Draw one Ising spin vector from the loaded model; returns the sample record
        (``spins``, ``completed_sweeps``, model hash, ...)."""
        from ._client import MessengerError
        job_id = self._request("POST", "/api/nano/sample", json={"burn_in": burn_in, "thin": thin}).json()["job_id"]
        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            operation = self.nano_status().get("operation") or {}
            if operation.get("id") == job_id and operation.get("status") not in ("running", "queued"):
                break
            time.sleep(.2)
        data = json.loads(self._request("GET", f"/api/jobs/{job_id}/outputs/measurements.json").content)
        if data.get("error"):
            raise MessengerError(409, data["error"])
        return data
