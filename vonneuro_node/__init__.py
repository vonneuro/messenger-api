"""
vonneuro-node
=============
Connects a piece of hardware to vonneuro.com (the hub).

The agent runs next to the hardware and dials out to the hub, so the machine needs no public
address. It publishes the devices a driver declares (specs and operations with bounded
parameters), takes jobs for them, runs them through the driver, uploads the result files and
reports usage. The driver decides what is safe for its hardware; the hub never overrides it.

    python -m vonneuro_node run --config node.toml

See HARDWARE_NETWORK.md for the design and drivers/simulated.py for a driver to copy.
"""
from .driver import DeviceSpec, Driver, Operation, RunContext, RunResult
from .schema import ParamError, check_operation, estimate_seconds, validate_params, validate_settings

PROTOCOL_VERSION = 1
__version__ = "0.1.0"

__all__ = [
    "Driver", "DeviceSpec", "Operation", "RunContext", "RunResult",
    "ParamError", "validate_params", "validate_settings", "check_operation", "estimate_seconds",
    "PROTOCOL_VERSION",
]
