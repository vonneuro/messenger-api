"""
vonneuro-messenger
=============
Python client for the vonNeuro Messenger job dispatch server.

Sync usage::

    from vonneuro_messenger import MessengerClient

    c = MessengerClient("http://workstation:8765", license_key="HRM-...")
    c.login("alice", "secret")

    job = c.submit("grape_opt", "julia", script="AIgor/run.jl",
                   args=["--qubits", "2"], env={"JULIA_NUM_THREADS": "4"})

    result = c.wait(job.id, on_update=lambda j: print(j.status))
    print(result.logs)

Async usage::

    from vonneuro_messenger import AsyncMessengerClient

    async with AsyncMessengerClient("http://workstation:8765") as c:
        await c.login("alice", "secret")
        job = await c.submit("grape_opt", "julia", script="run.jl")
        async for chunk in c.stream_logs(job.id):
            print(chunk, end="", flush=True)
"""

from ._client import MessengerClient, MessengerError
from ._async_client import AsyncMessengerClient
from ._bench import LiveSession
from ._models import Job, User, Node, DashboardStats, JobStatus, JobType

__all__ = [
    # Clients
    "MessengerClient",
    "AsyncMessengerClient",
    "LiveSession",
    # Models
    "Job",
    "User",
    "Node",
    "DashboardStats",
    # Constants
    "JobStatus",
    "JobType",
    # Exceptions
    "MessengerError",
]

__version__ = "0.1.0"
