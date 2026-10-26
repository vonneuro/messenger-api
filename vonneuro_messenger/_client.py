"""
Synchronous vonNeuro Messenger client.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import httpx

from ._auth import TUNNEL_HEADERS, TokenStore
from ._bench import BenchMixin
from ._devices import DevicesMixin
from ._models import Job, User, Node, DashboardStats, JobStatus


class MessengerError(Exception):
    """Raised when the server returns a non-2xx response."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


def _error_detail(resp: httpx.Response):
    """The server's 'detail', or the plain text of a non-JSON error (proxy pages, 500s)."""
    if not resp.content:
        return resp.reason_phrase
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:500]
    return body.get("detail", body) if isinstance(body, dict) else body


class MessengerClient(BenchMixin, DevicesMixin):
    """
    Synchronous client for the vonNeuro Messenger job dispatch server.

    Parameters
    ----------
    base_url:
        URL of the vonNeuro Messenger server, e.g. ``"http://workstation:8765"``.
    license_key:
        Optional license key sent as ``X-License-Key`` on every request.
    credentials_file:
        Path to persist JWT tokens between sessions.  If omitted, tokens are
        kept in memory only.
    timeout:
        Default request timeout in seconds.
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

    # ── Internal HTTP ─────────────────────────────────────────────────────────

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self._store.access_token}"}
        if self.license_key:
            h["X-License-Key"] = self.license_key
        if extra:
            h.update(extra)
        return h

    def _request(self, method: str, path: str, *, retry: bool = True, **kwargs) -> httpx.Response:
        with httpx.Client(base_url=self.base_url, timeout=self._timeout, headers=TUNNEL_HEADERS) as client:
            kwargs.setdefault("headers", {}).update(self._headers())
            resp = client.request(method, path, **kwargs)
            if resp.status_code == 401 and retry:
                if self._store.try_refresh(self.base_url):
                    kwargs["headers"].update(self._headers())
                    resp = client.request(method, path, **kwargs)
        if not resp.is_success:
            raise MessengerError(resp.status_code, _error_detail(resp))
        return resp

    # ── Auth ──────────────────────────────────────────────────────────────────

    def register(self, username: str, email: str, password: str, github_username: str | None = None,
                 purpose: str | None = None) -> User:
        """Create an account, then log in to it. Usernames are 3-32 characters (letters, digits,
        ``_ . -``); passwords 8-72 characters. New accounts start with an allowance of bench time.
        With ``github_username`` it also requests access to the private client repository; an
        admin invites that GitHub account by hand."""
        resp = httpx.post(f"{self.base_url}/api/auth/register", timeout=self._timeout, headers=TUNNEL_HEADERS,
                          json={"username": username, "email": email, "password": password,
                                "github_username": github_username, "purpose": purpose})
        if not resp.is_success:
            detail = _error_detail(resp)
            raise MessengerError(resp.status_code, detail)
        return self.login(username, password)

    def login(self, username: str, password: str) -> User:
        """Log in and return the current user.  Stores tokens automatically."""
        resp = httpx.post(
            f"{self.base_url}/api/auth/login",
            data={"username": username, "password": password},
            headers={"Content-Type": "application/x-www-form-urlencoded", **TUNNEL_HEADERS},
            timeout=self._timeout,
        )
        if not resp.is_success:
            detail = _error_detail(resp)
            raise MessengerError(resp.status_code, detail)
        d = resp.json()
        self._store.set(d["access_token"], d["refresh_token"])
        return self.me()

    def request_repo_access(self, github_username: str, purpose: str | None = None) -> User:
        """Ask for the private client repository for this GitHub account. ``me().github_access``
        then reads "requested", and "invited" once an admin has sent the GitHub invitation."""
        return User.from_dict(self._request("POST", "/api/auth/repo-access",
                                            json={"github_username": github_username, "purpose": purpose}).json())

    def logout(self) -> None:
        """Clear locally stored tokens."""
        self._store.clear()

    def me(self) -> User:
        """Return the currently authenticated user."""
        return User.from_dict(self._request("GET", "/api/auth/me").json())

    # ── Jobs ──────────────────────────────────────────────────────────────────

    def submit(
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
        """
        Submit a job to the server and return the created :class:`Job`.

        Parameters
        ----------
        name:
            Human-readable label for the job.
        job_type:
            Must match a key in ``runners.toml`` on the target server.
        script:
            Path to the script on the target machine.
        args:
            List of command-line arguments passed to the script.
        env:
            Extra environment variables, e.g. ``{"JULIA_NUM_THREADS": "4"}``.
        command:
            For shell runners (``is_command = true``), the raw command string.
        project:
            For Julia jobs, the ``--project`` path (default: current dir).
        scheduler:
            Override the server default scheduler
            (``"subprocess"`` / ``"celery"`` / ``"slurm"``).
        node_id:
            ID of a worker node to run the job on.  ``None`` runs on the
            server itself.
        priority:
            Integer priority hint (higher = sooner, when supported).
        timeout_seconds:
            Maximum wall-clock seconds the job may run.  The server kills it
            and marks it FAILED if this limit is exceeded.  ``None`` = no limit.
        attach_files:
            Local file paths to upload and attach to the job.
        attach_data:
            Arbitrary Python object serialised as JSON and attached.
        """
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
            "name":      name,
            "job_type":  job_type,
            "payload":   payload,
            "priority":  priority,
        }
        if scheduler is not None:
            body["scheduler"] = scheduler
        if node_id is not None:
            body["node_id"] = node_id
        if timeout_seconds is not None:
            body["timeout_seconds"] = timeout_seconds

        job = Job.from_dict(
            self._request("POST", "/api/jobs", json=body).json()
        )

        if attach_files:
            for path in attach_files:
                self._upload_file(job.id, path)
        if attach_data is not None:
            self._upload_json(job.id, attach_data)

        return job

    def get_job(self, job_id: int) -> Job:
        """Fetch the latest state of a job (syncs from worker if remote)."""
        return Job.from_dict(self._request("GET", f"/api/jobs/{job_id}").json())

    def list_jobs(self) -> list[Job]:
        """Return all jobs for the current user, newest first."""
        return [Job.from_dict(d) for d in self._request("GET", "/api/jobs").json()]

    def cancel(self, job_id: int) -> None:
        """Cancel a queued or running job."""
        self._request("DELETE", f"/api/jobs/{job_id}")

    # ── Blocking wait ─────────────────────────────────────────────────────────

    def wait(
        self,
        job_id: int,
        poll_interval: float = 3.0,
        timeout: float = 3600.0,
        on_update: Optional[Callable[[Job], None]] = None,
    ) -> Job:
        """
        Block until *job_id* reaches a terminal state and return the final
        :class:`Job`.

        Parameters
        ----------
        poll_interval:
            Seconds between status checks, used only with servers that lack the long-poll
            endpoint (``/api/jobs/{id}/wait``); otherwise each request returns when the job ends
            (or after 25 s), so a long sweep costs one request per 25 s.
        timeout:
            Maximum seconds to wait before raising :class:`TimeoutError`.
        on_update:
            Called with the :class:`Job` whenever its status changes.
        """
        deadline    = time.monotonic() + timeout
        last_status = None
        long_poll   = True   # the server answers when the job changes (at most 25 s per request)

        while time.monotonic() < deadline:
            if long_poll:
                left = max(0.0, min(25.0, deadline - time.monotonic()))
                try:
                    job = Job.from_dict(self._request("GET", f"/api/jobs/{job_id}/wait",
                                                      params={"timeout": left}, timeout=left + 15).json())
                except MessengerError as error:
                    if error.status_code not in (404, 405):
                        raise
                    long_poll = False   # an older server: poll instead
                    continue
            else:
                job = self.get_job(job_id)
            if job.status != last_status:
                last_status = job.status
                if on_update:
                    on_update(job)
            if job.is_terminal:
                return job
            if not long_poll:
                time.sleep(poll_interval)

        raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")

    def stream_logs(self, job_id: int, poll_interval: float = 2.0) -> Iterator[str]:
        """
        Generator that yields new log lines as they appear.
        Stops when the job reaches a terminal state.

        Usage::

            for line in client.stream_logs(job.id):
                print(line, end="")
        """
        seen = 0
        while True:
            job  = self.get_job(job_id)
            text = job.logs or ""
            if len(text) > seen:
                yield text[seen:]
                seen = len(text)
            if job.is_terminal:
                return
            time.sleep(poll_interval)

    # ── VOLT processing ───────────────────────────────────────────────────────

    def volt_programs(self) -> list[dict]:
        """
        List the VOLT programs this server can run as ``volt_process`` jobs.

        Returns dicts with ``name`` and ``signature`` (the program's
        ``process name(arg: type, ...)`` line).
        """
        return self._request("GET", "/api/jobs/volt-programs").json()["programs"]

    def submit_volt(
        self,
        program: str,
        inputs: dict[str, Any],
        *,
        name: Optional[str] = None,
        node_id: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Job:
        """Submit a ``volt_process`` job without waiting. See :meth:`run_volt`."""
        body: dict[str, Any] = {"name": name or f"VOLT {program}", "job_type": "volt_process",
                                "payload": {"program": program, "inputs": inputs}}
        if node_id is not None:
            body["node_id"] = node_id
        if timeout_seconds is not None:
            body["timeout_seconds"] = timeout_seconds
        return Job.from_dict(self._request("POST", "/api/jobs", json=body).json())

    def run_volt(
        self,
        program: str,
        inputs: dict[str, Any],
        *,
        name: Optional[str] = None,
        node_id: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
        poll_interval: float = 0.5,
        wait_timeout: float = 300.0,
    ) -> dict:
        """
        Run a named VOLT program on the server and return its reply.

        The reply is the job's ``result.json``: ``{"ok": True, "program": ...,
        "source_sha256": ..., "result": {"kind": "scalar", "value": ...}, ...}``.
        A program error returns ``{"ok": False, "error": ...}``.

        Example::

            reply = client.run_volt("device22_current_level", {
                "samples": [1e-6, -2e-6, 3e-6], "baseline_rms_a": 0, "scale_a": 5e-6})
            print(reply["result"]["value"])
        """
        import json
        job = self.submit_volt(program, inputs, name=name, node_id=node_id, timeout_seconds=timeout_seconds)
        job = self.wait(job.id, poll_interval=poll_interval, timeout=wait_timeout)
        try:
            response = self._request("GET", f"/api/jobs/{job.id}/outputs/result.json")
        except MessengerError as error:
            raise MessengerError(error.status_code,
                              f"Job {job.id} ended {job.status} without result.json: {job.logs or ''}"[:500]) from error
        reply = json.loads(response.content)
        reply["messenger_job"] = {"id": job.id, "status": str(job.status)}
        return reply

    # ── Files ─────────────────────────────────────────────────────────────────

    def upload_file(self, job_id: int, path: Path) -> dict:
        """Attach a local file to an existing job."""
        return self._upload_file(job_id, path)

    def _upload_file(self, job_id: int, path: Path) -> dict:
        with open(path, "rb") as f:
            resp = self._request(
                "POST", f"/api/jobs/{job_id}/files",
                files={"file": (path.name, f, "application/octet-stream")},
                headers={},   # _request merges auth headers
            )
        return resp.json()

    def _upload_json(self, job_id: int, data: Any, filename: str = "payload.json") -> dict:
        import io, json
        buf = io.BytesIO(json.dumps(data).encode())
        resp = self._request(
            "POST", f"/api/jobs/{job_id}/files",
            files={"file": (filename, buf, "application/json")},
            headers={},
        )
        return resp.json()

    # ── Output files (results) ────────────────────────────────────────────────

    def list_files(self, job_id: int) -> list[dict]:
        """
        List output files produced by a job.

        Returns a list of dicts with keys: name, size, modified, mime.

        Files are available as soon as the job finishes.  Scripts should write
        to the directory in ``$MESSENGER_OUTPUT_DIR`` (injected automatically).

        Example::

            files = client.list_files(job.id)
            for f in files:
                print(f["name"], f["size"])
        """
        return self._request("GET", f"/api/jobs/{job_id}/outputs").json()

    def download_file(self, job_id: int, filename: str, dest: Optional[Path] = None) -> Path:
        """
        Download a single output file and return its local path.

        Parameters
        ----------
        job_id:
            The job that produced the file.
        filename:
            The file's name as returned by :meth:`list_files`.
        dest:
            Local path to write to.  Defaults to ``./filename`` in the
            current working directory.

        Example::

            path = client.download_file(job.id, "result.h5")
            import h5py
            with h5py.File(path) as f:
                data = f["dataset"][:]
        """
        resp = self._request("GET", f"/api/jobs/{job_id}/outputs/{filename}")
        out  = Path(dest) if dest else Path(filename)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(resp.content)
        return out

    def get_results(
        self,
        job_id: int,
        output_dir: "str | Path" = ".",
        *,
        pattern: str = "*",
    ) -> list[Path]:
        """
        Download **all** output files for a job into ``output_dir``.

        Parameters
        ----------
        job_id:
            The completed job.
        output_dir:
            Local directory to save files in (created if absent).
        pattern:
            Glob-style filter, e.g. ``"*.h5"`` or ``"result_*"``.
            Default ``"*"`` downloads everything.

        Returns
        -------
        list[Path]
            Local paths of all downloaded files.

        Example::

            result_paths = client.get_results(job.id, output_dir="./results/run_42")
            # result_paths == [PosixPath('results/run_42/output.h5'), ...]
        """
        import fnmatch
        dest_dir = Path(output_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        files = self.list_files(job_id)
        paths = []
        for f in files:
            if fnmatch.fnmatch(f["name"], pattern):
                p = self.download_file(job_id, f["name"], dest_dir / f["name"])
                paths.append(p)
        return paths

    def delete_file(self, job_id: int, filename: str) -> None:
        """Delete an output file from the server to free disk space."""
        self._request("DELETE", f"/api/jobs/{job_id}/outputs/{filename}")

    # ── Nodes ─────────────────────────────────────────────────────────────────

    def list_nodes(self) -> list[Node]:
        """Return registered worker nodes."""
        return [Node.from_dict(d) for d in self._request("GET", "/api/nodes").json()]

    # ── Dashboard ─────────────────────────────────────────────────────────────

    def dashboard(self) -> DashboardStats:
        """Return aggregate job statistics for the current user."""
        return DashboardStats.from_dict(self._request("GET", "/api/dashboard").json())
