"""The agent: dials out to the hub, takes jobs for this node's devices and runs them through the driver.

Protocol (HTTP, every request signed with the node secret, see signing.py):

    POST /api/agent/hello                 devices and operations      -> node and device ids
    POST /api/agent/status                health per device (every status_interval s)
    POST /api/agent/jobs/claim?wait=25    long poll                   -> one job, settings to apply, or 204
    POST /api/agent/settings              how applying them went
    POST /api/agent/jobs/{id}/reject      the driver's reason
    POST /api/agent/jobs/{id}/progress    renews the lease            -> {"cancel": bool}
    PUT  /api/agent/jobs/{id}/outputs/{n} one result file (raw bytes)
    POST /api/agent/jobs/{id}/done        exit code, usage, energy, logs

A job whose lease is not renewed (agent gone) is failed by the hub and billed up to its last progress.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from . import PROTOCOL_VERSION, __version__
from .driver import Driver, RunContext, RunResult
from .schema import ParamError, validate_params
from .signing import headers as sign

log = logging.getLogger("vonneuro_node")

PROGRESS_INTERVAL_S = 10.0
FILE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILES = 64


class HubError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


class Agent:
    def __init__(self, hub_url: str, node_id: int, secret: str, driver: Driver, *,
                 http: httpx.Client | None = None, poll_wait: float = 25.0, status_interval: float = 30.0,
                 work_dir: Path | None = None, journal: Path | None = None):
        self.node_id, self.secret, self.driver = int(node_id), secret, driver
        self.http = http or httpx.Client(base_url=hub_url.rstrip("/"), timeout=poll_wait + 15, trust_env=False)
        self.poll_wait, self.status_interval = poll_wait, status_interval
        self.work_dir = Path(work_dir) if work_dir else None
        self.journal = Path(journal) if journal else None      # the owner's own record of every job run here
        self.specs: dict[str, dict] = {}
        self._stop = threading.Event()

    # ── transport ────────────────────────────────────────────────────────────

    def _call(self, method: str, path: str, *, data=None, content: bytes | None = None, query: dict | None = None,
              timeout: float | None = None) -> httpx.Response:
        target = path + ("?" + urlencode(query) if query else "")
        body = content if content is not None else (b"" if data is None else
                                                    json.dumps(data, allow_nan=False, separators=(",", ":")).encode())
        request_headers = sign(self.node_id, self.secret, method, target, body)
        request_headers["Content-Type"] = "application/octet-stream" if content is not None else "application/json"
        request_headers["ngrok-skip-browser-warning"] = "1"
        response = self.http.request(method, target, content=body, headers=request_headers,
                                     **({"timeout": timeout} if timeout else {}))
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise HubError(response.status_code, str(detail))
        return response

    # ── node ─────────────────────────────────────────────────────────────────

    def hello(self) -> dict:
        devices = [spec.to_dict() for spec in self.driver.devices()]
        self.specs = {device["slug"]: device for device in devices}
        reply = self._call("POST", "/api/agent/hello", data={
            "protocol": PROTOCOL_VERSION, "agent_version": __version__, "devices": devices}).json()
        log.info("connected as node %s with %d device(s)", reply.get("node", {}).get("name"), len(devices))
        return reply

    def send_status(self) -> None:
        report = {}
        for slug in self.specs:
            try:
                report[slug] = {"enabled": self._enabled(slug), **(self.driver.status(slug) or {})}
            except Exception as exc:                       # a broken status never stops the agent
                report[slug] = {"error": str(exc)[:500]}
        self._call("POST", "/api/agent/status", data={"devices": report})

    def _enabled(self, slug: str) -> bool:
        for spec in self.driver.devices():
            if spec.slug == slug:
                return spec.enabled
        return False

    # ── jobs ─────────────────────────────────────────────────────────────────

    def claim(self, wait: float | None = None) -> dict | None:
        wait = self.poll_wait if wait is None else wait
        response = self._call("POST", "/api/agent/jobs/claim", query={"wait": f"{wait:g}"}, timeout=wait + 15)
        return None if response.status_code == 204 else response.json()

    def run_once(self, wait: float | None = None) -> bool:
        """Claim and run at most one job, or apply settings changed on the website. Returns whether
        anything was handled."""
        job = self.claim(wait)
        if not job:
            return False
        if "settings" in job:
            self.apply_settings(job["settings"])
        else:
            self.handle(job)
        return True

    def apply_settings(self, changes: dict) -> None:
        """Settings a node admin changed (between jobs): the driver applies them, the hub learns the outcome,
        and the devices are published again with the values now in force."""
        results = {}
        for slug, values in changes.items():
            try:
                self.driver.apply_settings(slug, values)
                results[slug] = {"ok": True}
                log.info("settings of %s changed: %s", slug, values)
            except Exception as exc:
                results[slug] = {"ok": False, "error": str(exc)[:500]}
                log.warning("settings of %s refused: %s", slug, exc)
            self._record({"device": slug, "params": values}, "settings", result=results[slug])
        self.hello()                     # publish the values in force first, so the hub never shows a stale value
        self._call("POST", "/api/agent/settings", data={"results": results})

    def handle(self, job: dict) -> None:
        job_id, slug, name = job["id"], job["device"], job["operation"]
        directory = Path(tempfile.mkdtemp(prefix=f"vn-job-{job_id}-", dir=self.work_dir))
        ctx = RunContext(job_id=job_id, output_dir=directory, timeout_s=job.get("timeout_seconds"),
                         privileged=bool(job.get("privileged")))
        try:
            spec = self.specs.get(slug) or {}
            operation = next((op for op in spec.get("operations", []) if op["name"] == name), None)
            if operation is None:
                return self._reject(job_id, f"This node does not offer {name} on {slug}")
            try:
                params = validate_params(operation.get("params", {}), job.get("params"))
            except ParamError as exc:
                return self._reject(job_id, str(exc))
            try:
                problem = self.driver.check(slug, name, params, ctx)
            except Exception as exc:
                problem = f"The driver could not check this job: {exc}"
            if problem:
                return self._reject(job_id, problem, job)
            result = self._run(slug, name, params, ctx)
            self._record(job, "cancelled" if ctx.cancelled() else "ran", exit_code=result.exit_code,
                         usage_seconds=round(result.usage_seconds, 3), energy_j=result.energy_j)
            self._upload(job_id, directory)
            self._call("POST", f"/api/agent/jobs/{job_id}/done", data={
                "exit_code": result.exit_code, "usage_seconds": result.usage_seconds,
                "energy_j": result.energy_j, "logs": result.logs[-200_000:]})
        except HubError as exc:
            log.warning("job %s: hub refused %s", job_id, exc)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def _reject(self, job_id: int, reason: str, job: dict | None = None) -> None:
        log.info("job %s refused: %s", job_id, reason)
        self._record(job or {"id": job_id}, "refused", reason=reason)
        self._call("POST", f"/api/agent/jobs/{job_id}/reject", data={"reason": reason[:2000]})

    def _record(self, job: dict, outcome: str, **details) -> None:
        if not self.journal:
            return
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "job": job.get("id"), "device": job.get("device"),
                 "operation": job.get("operation"), "params": job.get("params"), "outcome": outcome, **details}
        try:
            with self.journal.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, default=str) + "\n")
        except OSError as exc:
            log.warning("journal not written (%s)", exc)

    def _run(self, slug: str, name: str, params: dict, ctx: RunContext) -> RunResult:
        outcome: dict = {}

        def work():
            started = time.monotonic()
            try:
                outcome["result"] = self.driver.run(slug, name, params, ctx)
            except Exception as exc:
                outcome["result"] = RunResult(exit_code=1, usage_seconds=time.monotonic() - started,
                                              logs=f"[driver error] {type(exc).__name__}: {exc}")

        worker = threading.Thread(target=work, name=f"job-{ctx.job_id}", daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(PROGRESS_INTERVAL_S)
            if worker.is_alive():
                try:
                    if self._call("POST", f"/api/agent/jobs/{ctx.job_id}/progress", data={}).json().get("cancel"):
                        ctx.cancel()
                except (HubError, httpx.HTTPError) as exc:
                    log.warning("job %s: progress not delivered (%s)", ctx.job_id, exc)
        return outcome["result"]

    def _upload(self, job_id: int, directory: Path) -> None:
        files = sorted(path for path in directory.iterdir() if path.is_file())[:MAX_FILES]
        for path in files:
            if not FILE_NAME.fullmatch(path.name) or path.stat().st_size > MAX_FILE_BYTES:
                log.warning("job %s: output %s skipped (name or size)", job_id, path.name)
                continue
            self._call("PUT", f"/api/agent/jobs/{job_id}/outputs/{path.name}", content=path.read_bytes())

    # ── main loop ────────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._stop.set()

    def serve_forever(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self.hello()
                self.send_status()
                break
            except (HubError, httpx.HTTPError) as exc:
                log.warning("hub not reachable yet (%s); retrying in %.0f s", exc, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)
        threading.Thread(target=self._status_loop, name="status", daemon=True).start()
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self.run_once()
                backoff = 1.0
            except HubError as exc:
                if exc.status in (401, 403, 409):          # restarted hub or changed devices: introduce again
                    try:
                        self.hello()
                    except Exception:
                        pass
                log.warning("hub: %s; retrying in %.0f s", exc, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)
            except httpx.HTTPError as exc:
                log.warning("hub not reachable (%s); retrying in %.0f s", exc, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)

    def _status_loop(self) -> None:
        published = json.dumps([spec.to_dict() for spec in self.driver.devices()], sort_keys=True)
        while not self._stop.wait(self.status_interval):
            try:
                current = json.dumps([spec.to_dict() for spec in self.driver.devices()], sort_keys=True)
                if current != published:                   # the owner changed a device: publish it again
                    self.hello()
                    published = current
                self.send_status()
            except Exception as exc:
                log.warning("status not delivered (%s)", exc)
