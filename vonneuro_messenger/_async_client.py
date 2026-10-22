"""
Async vonNeuro Messenger client — mirrors MessengerClient but uses httpx.AsyncClient.
Useful when vonNeuro Messenger is called from async frameworks (FastAPI, asyncio scripts,
Jupyter notebooks with %autoawait, etc.).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

import httpx

from ._auth import TUNNEL_HEADERS, TokenStore
from ._models import Job, User, Node, DashboardStats
from ._client import MessengerError, _error_detail


class AsyncMessengerClient:
    """
    Async client for the vonNeuro Messenger job dispatch server.

    Meant to be used as an async context manager::

        async with AsyncMessengerClient("http://workstation:8765") as c:
            await c.login("alice", "secret")
            job = await c.submit("pulse_opt", "julia", script="run.jl")
            result = await c.wait(job.id)
    """

    def __init__(
        self,
        base_url: str,
        license_key: Optional[str] = None,
        credentials_file: Optional[Path] = None,
        timeout: float = 30.0,
    ):
        self.base_url    = base_url.rstrip("/")
        self.license_key = license_key
        self._store      = TokenStore(credentials_file)
        self._timeout    = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "AsyncMessengerClient":
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout, headers=TUNNEL_HEADERS)
        return self

    async def __aexit__(self, *_):
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Internal HTTP ─────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        h = {"Authorization": f"Bearer {self._store.access_token}"}
        if self.license_key:
            h["X-License-Key"] = self.license_key
        return h

    async def _request(self, method: str, path: str, *, retry: bool = True, **kwargs) -> httpx.Response:
        client = self._client or httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout, headers=TUNNEL_HEADERS)
        kwargs.setdefault("headers", {}).update(self._headers())
        try:
            resp = await client.request(method, path, **kwargs)
            if resp.status_code == 401 and retry:
                if await self._store.async_try_refresh(self.base_url):
                    kwargs["headers"].update(self._headers())
                    resp = await client.request(method, path, **kwargs)
            if not resp.is_success:
                detail = _error_detail(resp)
                raise MessengerError(resp.status_code, detail)
            return resp
        finally:
            if not self._client:        # we created a one-shot client
                await client.aclose()

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def login(self, username: str, password: str) -> User:
        client = httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout, headers=TUNNEL_HEADERS)
        try:
            resp = await client.post(
                "/api/auth/login",
                data={"username": username, "password": password},
                headers={"Content-Type": "application/x-www-form-urlencoded", **TUNNEL_HEADERS},
            )
        finally:
            await client.aclose()
        if not resp.is_success:
            raise MessengerError(resp.status_code, _error_detail(resp))
        d = resp.json()
        self._store.set(d["access_token"], d["refresh_token"])
        return await self.me()

    def logout(self) -> None:
        self._store.clear()

    async def me(self) -> User:
        return User.from_dict((await self._request("GET", "/api/auth/me")).json())

    # ── Jobs ──────────────────────────────────────────────────────────────────

    async def submit(
        self,
        name: str,
        job_type: str,
        *,
        script: str = "",
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        command: str = "",
        project: str = ".",
        scheduler: Optional[str] = None,
        node_id: Optional[int] = None,
        priority: int = 0,
        timeout_seconds: Optional[int] = None,
        attach_files: list[Path] | None = None,
        attach_data: Any = None,
    ) -> Job:
        """Submit a job.  ``timeout_seconds=N`` kills the job after N seconds."""
        payload: dict[str, Any] = {}
        if script:
            payload["script"] = script
        if args:
            payload["args"] = args
        if env:
            payload["env"] = env
        if command:
            payload["command"] = command
        if job_type == "julia":
            payload["project"] = project

        body: dict[str, Any] = {
            "name":     name,
            "job_type": job_type,
            "payload":  payload,
            "priority": priority,
        }
        if scheduler is not None:
            body["scheduler"] = scheduler
        if node_id is not None:
            body["node_id"] = node_id
        if timeout_seconds is not None:
            body["timeout_seconds"] = timeout_seconds

        job = Job.from_dict((await self._request("POST", "/api/jobs", json=body)).json())

        if attach_files:
            for path in attach_files:
                await self._upload_file(job.id, path)
        if attach_data is not None:
            await self._upload_json(job.id, attach_data)

        return job

    async def get_job(self, job_id: int) -> Job:
        return Job.from_dict((await self._request("GET", f"/api/jobs/{job_id}")).json())

    async def list_jobs(self) -> list[Job]:
        return [Job.from_dict(d) for d in (await self._request("GET", "/api/jobs")).json()]

    async def cancel(self, job_id: int) -> None:
        await self._request("DELETE", f"/api/jobs/{job_id}")

    # ── Async wait ────────────────────────────────────────────────────────────

    async def wait(
        self,
        job_id: int,
        poll_interval: float = 3.0,
        timeout: float = 3600.0,
        on_update: Optional[Callable[[Job], None]] = None,
    ) -> Job:
        """Async version of wait — non-blocking between polls."""
        import time
        deadline    = time.monotonic() + timeout
        last_status = None

        while time.monotonic() < deadline:
            job = await self.get_job(job_id)
            if job.status != last_status:
                last_status = job.status
                if on_update:
                    on_update(job)
            if job.is_terminal:
                return job
            await asyncio.sleep(poll_interval)

        raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")

    async def stream_logs(self, job_id: int, poll_interval: float = 2.0) -> AsyncIterator[str]:
        """Async generator that yields new log chunks as they arrive."""
        seen = 0
        while True:
            job  = await self.get_job(job_id)
            text = job.logs or ""
            if len(text) > seen:
                yield text[seen:]
                seen = len(text)
            if job.is_terminal:
                return
            await asyncio.sleep(poll_interval)

    # ── VOLT processing ───────────────────────────────────────────────────────

    async def volt_programs(self) -> list[dict]:
        """List the VOLT programs this server can run as ``volt_process`` jobs."""
        return (await self._request("GET", "/api/jobs/volt-programs")).json()["programs"]

    async def submit_volt(self, program: str, inputs: dict[str, Any], *, name: Optional[str] = None,
                          node_id: Optional[int] = None, timeout_seconds: Optional[int] = None) -> Job:
        """Submit a ``volt_process`` job without waiting."""
        body: dict[str, Any] = {"name": name or f"VOLT {program}", "job_type": "volt_process",
                                "payload": {"program": program, "inputs": inputs}}
        if node_id is not None:
            body["node_id"] = node_id
        if timeout_seconds is not None:
            body["timeout_seconds"] = timeout_seconds
        return Job.from_dict((await self._request("POST", "/api/jobs", json=body)).json())

    async def run_volt(self, program: str, inputs: dict[str, Any], *, name: Optional[str] = None,
                       node_id: Optional[int] = None, timeout_seconds: Optional[int] = None,
                       poll_interval: float = 0.5, wait_timeout: float = 300.0) -> dict:
        """Run a named VOLT program on the server and return its ``result.json`` reply."""
        import json
        job = await self.submit_volt(program, inputs, name=name, node_id=node_id, timeout_seconds=timeout_seconds)
        job = await self.wait(job.id, poll_interval=poll_interval, timeout=wait_timeout)
        try:
            response = await self._request("GET", f"/api/jobs/{job.id}/outputs/result.json")
        except MessengerError as error:
            raise MessengerError(error.status_code,
                              f"Job {job.id} ended {job.status} without result.json: {job.logs or ''}"[:500]) from error
        reply = json.loads(response.content)
        reply["messenger_job"] = {"id": job.id, "status": str(job.status)}
        return reply

    # ── Files ─────────────────────────────────────────────────────────────────

    async def upload_file(self, job_id: int, path: Path) -> dict:
        return await self._upload_file(job_id, path)

    async def _upload_file(self, job_id: int, path: Path) -> dict:
        with open(path, "rb") as f:
            resp = await self._request(
                "POST", f"/api/jobs/{job_id}/files",
                files={"file": (path.name, f, "application/octet-stream")},
                headers={},
            )
        return resp.json()

    async def _upload_json(self, job_id: int, data: Any, filename: str = "payload.json") -> dict:
        import io, json
        buf = io.BytesIO(json.dumps(data).encode())
        resp = await self._request(
            "POST", f"/api/jobs/{job_id}/files",
            files={"file": (filename, buf, "application/json")},
            headers={},
        )
        return resp.json()

    # ── Output files (results) ────────────────────────────────────────────────

    async def list_files(self, job_id: int) -> list[dict]:
        """List output files produced by a job (name, size, modified, mime)."""
        return (await self._request("GET", f"/api/jobs/{job_id}/outputs")).json()

    async def download_file(self, job_id: int, filename: str, dest: Optional[Path] = None) -> Path:
        """Download a single output file and return its local path."""
        resp = await self._request("GET", f"/api/jobs/{job_id}/outputs/{filename}")
        out  = Path(dest) if dest else Path(filename)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(resp.content)
        return out

    async def get_results(
        self,
        job_id: int,
        output_dir: "str | Path" = ".",
        *,
        pattern: str = "*",
    ) -> list[Path]:
        """
        Download all output files for a job into ``output_dir``.
        Optionally filter by glob pattern (e.g. ``"*.h5"``).
        """
        import fnmatch
        dest_dir = Path(output_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        files = await self.list_files(job_id)
        paths = []
        for f in files:
            if fnmatch.fnmatch(f["name"], pattern):
                p = await self.download_file(job_id, f["name"], dest_dir / f["name"])
                paths.append(p)
        return paths

    async def delete_file(self, job_id: int, filename: str) -> None:
        """Delete an output file from the server."""
        await self._request("DELETE", f"/api/jobs/{job_id}/outputs/{filename}")

    # ── Nodes / dashboard ─────────────────────────────────────────────────────

    async def list_nodes(self) -> list[Node]:
        return [Node.from_dict(d) for d in (await self._request("GET", "/api/nodes")).json()]

    async def dashboard(self) -> DashboardStats:
        return DashboardStats.from_dict((await self._request("GET", "/api/dashboard")).json())
