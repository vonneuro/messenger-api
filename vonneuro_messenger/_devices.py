"""Devices on the vonNeuro network: any owner's hardware, addressed as ``owner/device``.

Each device declares operations with typed, bounded parameters; the server checks them, queues
the job for the device's node, and the node's driver decides whether it is safe to run::

    array = client.device("admin/memristor-array")
    print(array.operations["iv_sweep"]["params"])
    data = array.run("iv_sweep", channel=3, amplitude=0.2, frequency=10)
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ._models import Job, JobStatus


class Device:
    def __init__(self, client, data: dict):
        self._client = client
        self._load(data)

    def _load(self, data: dict) -> None:
        self.data = data
        self.ref: str = data["ref"]
        self.name: str = data.get("name", "")
        self.kind: str = data.get("kind", "")
        self.specs: dict = data.get("specs") or {}
        self.operations: dict[str, dict] = {op["name"]: op for op in data.get("operations") or []}

    @property
    def status(self) -> str:
        """online, offline (its node is not connected) or disabled."""
        return self.data.get("status", "unknown")

    def __repr__(self) -> str:
        return f"Device({self.ref!r}, {self.kind}, {self.status}, operations={list(self.operations)})"

    def refresh(self) -> "Device":
        self._load(self._client._request("GET", f"/api/devices/{self.ref}").json())
        return self

    def submit(self, operation: str, *, name: Optional[str] = None, timeout_seconds: Optional[int] = None,
               **params: Any) -> Job:
        """Queue one operation and return the job at once (status queued)."""
        body = {"operation": operation, "params": params}
        if name:
            body["name"] = name
        if timeout_seconds:
            body["timeout_seconds"] = timeout_seconds
        return Job.from_dict(self._client._request("POST", f"/api/devices/{self.ref}/run", json=body).json())

    def result(self, job: Job, *, wait_timeout: Optional[float] = None) -> dict:
        """Wait for a submitted job and return its main result file (parsed), plus ``messenger_job``.
        Operations without a main result return ``{"files": [...]}``, ready for download_file()."""
        from ._client import MessengerError
        operation = self.operations.get((job.payload or {}).get("operation", ""), {})
        if wait_timeout is None:
            wait_timeout = max(180.0, float(operation.get("timeout_s", 60)) + 120.0)
        job = self._client.wait(job.id, poll_interval=0.5, timeout=wait_timeout)
        if job.status != JobStatus.DONE:
            raise MessengerError(500, f"Job {job.id} {job.status}: {(job.logs or '')[-400:]}")
        if operation.get("result"):
            data = json.loads(self._client._request("GET", f"/api/jobs/{job.id}/outputs/{operation['result']}").content)
        else:
            data = {"files": self._client._request("GET", f"/api/jobs/{job.id}/outputs").json()}
        data["messenger_job"] = {"id": job.id, "status": str(job.status)}
        return data

    def run(self, operation: str, *, name: Optional[str] = None, wait_timeout: Optional[float] = None,
            **params: Any) -> dict:
        """Run one operation and return its result (see :meth:`result`)."""
        return self.result(self.submit(operation, name=name, **params), wait_timeout=wait_timeout)

    # ── Time on approved-only devices ───────────────────────────────────────

    def request_access(self, hours: float, reason: str) -> dict:
        """Ask the owner of an approved-only device for time on it (up to 24 h per request)."""
        return self._client._request("POST", f"/api/devices/{self.ref}/access",
                                     json={"hours": hours, "reason": reason}).json()

    def access(self) -> list[dict]:
        """Your request and the time left on this device; for the owner, every request."""
        return self._client._request("GET", f"/api/devices/{self.ref}/access").json()

    # ── For the device's owner ──────────────────────────────────────────────

    def answer_access(self, request_id: int, approve: bool, hours: Optional[float] = None, answer: str = "") -> dict:
        body = {"approve": approve, "answer": answer, **({"hours": hours} if hours else {})}
        return self._client._request("POST", f"/api/devices/{self.ref}/access/{request_id}", json=body).json()

    def usage(self, days: int = 30) -> dict:
        """Who used the device, how long and with how much energy; the latest jobs."""
        return self._client._request("GET", f"/api/devices/{self.ref}/usage", params={"days": days}).json()

    def update(self, **changes: Any) -> "Device":
        """hub_disabled, visibility ("public", "approved", "private"), description, retention_days."""
        self._load(self._client._request("PATCH", f"/api/devices/{self.ref}", json=changes).json())
        return self

    @property
    def settings(self) -> dict:
        """For the node's admins: the settings its driver declares, each with its range, unit,
        default and the "value" in force (e.g. the bench's pulse limits)."""
        return self.data.get("settings") or {}

    def change_settings(self, **values: Any) -> "Device":
        """Node admins: change settings (None resets one), e.g. ``change_settings(pulse_max_v=1.0)``.
        The node applies them between jobs; ``refresh()`` shows the values once applied."""
        self._load(self._client._request("PUT", f"/api/devices/{self.ref}/settings", json=values).json())
        return self


class DevicesMixin:
    def devices(self) -> list[Device]:
        """Every device this account can use, on every node of the network."""
        return [Device(self, data) for data in self._request("GET", "/api/devices").json()]

    def device(self, ref: str) -> Device:
        """One device by ``owner/slug``, e.g. ``"admin/memristor-array"``."""
        return Device(self, self._request("GET", f"/api/devices/{ref}").json())

    # ── Nodes: register them, administer them ───────────────────────────────
    # Every account is a user; the owner of a node and the admins it adds manage its devices;
    # superadmins manage every node.

    def my_nodes(self, all: bool = False) -> list[dict]:
        """The nodes this account administers (``role``: owner, admin), with devices and admins.
        Superadmins pass ``all=True`` for every node on the hub."""
        return self._request("GET", "/api/nodes/mine", params={"all": "true"} if all else None).json()

    def pairing_code(self, name: str, description: str = "") -> dict:
        """A one-time code (15 minutes) to connect a machine: ``vonneuro-node pair --hub ... --code ...``.
        While registration needs approval, call :meth:`request_node` first."""
        return self._request("POST", "/api/nodes/pairing", json={"name": name, "description": description}).json()

    def request_node(self, name: str, reason: str, description: str = "") -> dict:
        """Ask a superadmin to approve registering a node; then :meth:`pairing_code` works."""
        return self._request("POST", "/api/nodes/requests",
                             json={"name": name, "reason": reason, "description": description}).json()

    def node_requests(self) -> list[dict]:
        """Your registration requests; for a superadmin, everyone's."""
        return self._request("GET", "/api/nodes/requests").json()

    def answer_node_request(self, request_id: int, approve: bool, answer: str = "") -> dict:
        """Superadmin: approve or deny a registration request."""
        return self._request("POST", f"/api/nodes/requests/{request_id}", json={"approve": approve, "answer": answer}).json()

    def add_node_admin(self, node_id: int, username: str) -> dict:
        """Owner or superadmin: make another account an admin of this node."""
        return self._request("POST", f"/api/nodes/{node_id}/admins", json={"username": username}).json()

    def remove_node_admin(self, node_id: int, username: str) -> None:
        """Owner or superadmin; an admin may remove themselves."""
        self._request("DELETE", f"/api/nodes/{node_id}/admins/{username}")
