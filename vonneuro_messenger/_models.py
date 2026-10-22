"""
Typed dataclasses mirroring the server's response schemas.
All factory methods accept raw dicts from the JSON API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class JobStatus:
    QUEUED    = "queued"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    CANCELLED = "cancelled"


class JobType:
    PYTHON = "python"
    JULIA  = "julia"
    SHELL  = "shell"
    REMOTE = "remote"
    VOLT_EXPERIMENT      = "volt_experiment"
    VOLT_PROCESS         = "volt_process"
    MESSENGER_INSTRUMENT = "messenger_instrument"


@dataclass
class Job:
    id:              int
    name:            str
    job_type:        str
    status:          str
    payload:         dict[str, Any]
    scheduler:       str | None  = None
    node_id:         int | None  = None
    backend_id:      str | None  = None
    logs:            str | None  = None
    exit_code:       int | None  = None
    timeout_seconds: int | None  = None
    created_at:      datetime | None = None
    started_at:      datetime | None = None
    completed_at:    datetime | None = None

    # ── Convenience properties ────────────────────────────────────────────────
    @property
    def is_terminal(self) -> bool:
        return self.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def succeeded(self) -> bool:
        return self.status == JobStatus.DONE

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def __repr__(self) -> str:
        return f"Job(id={self.id}, name={self.name!r}, status={self.status!r})"

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        return cls(
            id=d["id"],
            name=d["name"],
            job_type=d["job_type"],
            status=d["status"],
            payload=d.get("payload") or {},
            scheduler=d.get("scheduler"),
            node_id=d.get("node_id"),
            backend_id=d.get("backend_id"),
            logs=d.get("logs"),
            exit_code=d.get("exit_code"),
            timeout_seconds=d.get("timeout_seconds"),
            created_at=_dt(d.get("created_at")),
            started_at=_dt(d.get("started_at")),
            completed_at=_dt(d.get("completed_at")),
        )


@dataclass
class User:
    id:          int
    username:    str
    email:       str
    license_key: str | None
    is_active:   bool
    is_admin:    bool
    github_username: str | None = None
    github_access:   str | None = None   # None, "requested" or "invited"

    def __repr__(self) -> str:
        return f"User(id={self.id}, username={self.username!r})"

    @classmethod
    def from_dict(cls, d: dict) -> "User":
        return cls(
            id=d["id"],
            username=d["username"],
            email=d["email"],
            license_key=d.get("license_key"),
            is_active=d.get("is_active", True),
            is_admin=d.get("is_admin", False),
            github_username=d.get("github_username"),
            github_access=d.get("github_access"),
        )


@dataclass
class Node:
    id:             int
    name:           str
    url:            str
    status:         str
    schedulers:     list[str]
    max_concurrent: int
    is_active:      bool
    last_seen:      datetime | None
    created_at:     datetime | None

    def __repr__(self) -> str:
        return f"Node(id={self.id}, name={self.name!r}, status={self.status!r})"

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(
            id=d["id"],
            name=d["name"],
            url=d["url"],
            status=d.get("status", "unknown"),
            schedulers=d.get("schedulers") or [],
            max_concurrent=d.get("max_concurrent", 2),
            is_active=d.get("is_active", True),
            last_seen=_dt(d.get("last_seen")),
            created_at=_dt(d.get("created_at")),
        )


@dataclass
class DashboardStats:
    total:       int
    queued:      int
    running:     int
    done:        int
    failed:      int
    recent_jobs: list[Job] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "DashboardStats":
        return cls(
            total=d["total"],
            queued=d["queued"],
            running=d["running"],
            done=d["done"],
            failed=d["failed"],
            recent_jobs=[Job.from_dict(j) for j in d.get("recent_jobs", [])],
        )
