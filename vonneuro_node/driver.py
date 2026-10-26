"""The driver interface: what an owner writes to put their hardware on vonneuro.com.

A driver declares its devices and their operations, says whether a job is safe (check), and runs
it (run), writing result files into ctx.output_dir. The agent does everything else: talking to
the hub, leases, cancels, uploads and usage.
"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Operation:
    name: str
    summary: str = ""
    params: dict = field(default_factory=dict)     # name -> parameter spec (see schema.py)
    estimate: str | None = None                     # run time in seconds, e.g. "6 / frequency + 15"
    timeout_s: float = 60.0                         # default limit; the hub adds the estimate
    result: str | None = None                       # main JSON output, returned by client.device().run()
    outputs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value not in (None, [], "")}


@dataclass
class DeviceSpec:
    slug: str                                       # unique per owner: lowercase, digits, - and _
    name: str
    kind: str                                       # e.g. memristor, rc, pbit, mux, photonic, other
    description: str = ""
    specs: dict = field(default_factory=dict)       # free-form facts shown on the device page
    operations: list[Operation] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    enabled: bool = True                            # the owner's own switch, on the node
    # Settings the node's admins may change from the website (My hardware), e.g. limits:
    # name -> a parameter spec (schema.py) plus "value", the value in force now (None: the default).
    settings: dict = field(default_factory=dict)
    # The device's default page (Markdown): what it is and how to use it. Node admins can rewrite it
    # on the website; placeholders such as {{setting:NAME}}, {{operations}} and {{python}} stay live.
    page: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["operations"] = [operation.to_dict() for operation in self.operations]
        return data


@dataclass
class RunContext:
    job_id: int
    output_dir: Path
    timeout_s: float | None = None
    privileged: bool = False                        # submitted by the device owner or a hub admin
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()


@dataclass
class RunResult:
    exit_code: int
    usage_seconds: float
    logs: str = ""
    energy_j: float | None = None                   # measured work at the device, if known


class Driver:
    """Subclass this. Methods are called from the agent's worker thread, one job at a time."""

    def devices(self) -> list[DeviceSpec]:
        raise NotImplementedError

    def status(self, slug: str) -> dict:
        """Cheap health report for one device (never drives hardware)."""
        return {}

    def check(self, slug: str, operation: str, params: dict, ctx: RunContext) -> str | None:
        """Why this job must not run on the hardware, or None when it is safe."""
        return None

    def run(self, slug: str, operation: str, params: dict, ctx: RunContext) -> RunResult:
        raise NotImplementedError

    def apply_settings(self, slug: str, values: dict) -> None:
        """Apply settings a node admin changed on the website (already checked against the declared
        specs; None resets one to its default). Persist them; the next devices() reports them.
        Raise ValueError to refuse; the message is shown to the admin."""
        raise ValueError(f"{slug} has no settings")
