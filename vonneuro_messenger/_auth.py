import json
import os
from pathlib import Path
from typing import Optional

import httpx

# Sent with every request. The bench server is published through an ngrok tunnel whose free plan
# shows browsers an interstitial page; this header tells ngrok the caller is a program.
TUNNEL_HEADERS = {"ngrok-skip-browser-warning": "1"}


class TokenStore:
    """Caches JWT tokens in memory and optionally persists them to a file."""

    def __init__(self, credentials_path: Optional[Path | str] = None):
        self._access:  str = ""
        self._refresh: str = ""
        self._path = Path(credentials_path).expanduser() if credentials_path else None
        if self._path and self._path.exists():
            self._load()

    def _load(self):
        try:
            d = json.loads(self._path.read_text())
            self._access  = d.get("access_token", "")
            self._refresh = d.get("refresh_token", "")
        except Exception:
            pass

    def _save(self):
        if self._path:
            self._path.write_text(json.dumps({
                "access_token":  self._access,
                "refresh_token": self._refresh,
            }))
            os.chmod(self._path, 0o600)

    def set(self, access: str, refresh: str):
        self._access, self._refresh = access, refresh
        self._save()

    @property
    def access_token(self) -> str:
        return self._access

    def clear(self):
        self._access = self._refresh = ""
        if self._path and self._path.exists():
            self._path.unlink()

    def try_refresh(self, base_url: str) -> bool:
        if not self._refresh:
            return False
        try:
            resp = httpx.post(
                f"{base_url}/api/auth/refresh",
                params={"refresh_token": self._refresh},
                headers=TUNNEL_HEADERS,
                timeout=10,
            )
            if resp.is_success:
                d = resp.json()
                self.set(d["access_token"], d["refresh_token"])
                return True
        except Exception:
            pass
        return False

    async def async_try_refresh(self, base_url: str) -> bool:
        if not self._refresh:
            return False
        try:
            async with httpx.AsyncClient(timeout=10, headers=TUNNEL_HEADERS) as client:
                resp = await client.post(
                    f"{base_url}/api/auth/refresh",
                    params={"refresh_token": self._refresh},
                )
            if resp.is_success:
                d = resp.json()
                self.set(d["access_token"], d["refresh_token"])
                return True
        except Exception:
            pass
        return False
