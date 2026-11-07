"""HTTP client for the simulator. The only way the controller learns anything about the world.

Retry policy:
- Reads (GET) are retried on transport errors and 5xx with backoff.
- Writes are never blindly retried. Each write carries an idempotency key we persisted first
  (proposal_id) or is reconciled by reading state back (advance, apply). See governance.py.
"""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("ctrl.client")


class SimError(Exception):
    def __init__(self, status: int, code: str, detail, path: str):
        super().__init__(f"{status} {code} on {path}: {detail}")
        self.status, self.code, self.detail, self.path = status, code, detail, path


class TransportError(Exception):
    """The request may or may not have reached the simulator."""


class Sim:
    def __init__(self, base_url: str, timeout: float = 300.0):
        self.http = httpx.Client(base_url=base_url, timeout=timeout)
        self.calls = 0
        self.errors_5xx = 0

    # ------------------------------------------------------------ plumbing
    def _req(self, method: str, path: str, *, retry: bool, **kw):
        delay = 0.5
        for attempt in range(8 if retry else 1):
            try:
                self.calls += 1
                r = self.http.request(method, path, **kw)
            except httpx.TransportError as e:
                if not retry:
                    raise TransportError(f"{method} {path}: {e}") from e
                log.warning("transport error on %s %s (%s), retrying in %.1fs", method, path, e, delay)
                time.sleep(delay)
                delay = min(delay * 2, 10)
                continue
            if r.status_code >= 500:
                self.errors_5xx += 1
                if retry and attempt < 7:
                    log.warning("%s on %s %s, retrying in %.1fs", r.status_code, method, path, delay)
                    time.sleep(delay)
                    delay = min(delay * 2, 10)
                    continue
            if r.status_code >= 400:
                try:
                    body = r.json()
                except ValueError:
                    body = {"error": "http_error", "detail": r.text[:300]}
                raise SimError(r.status_code, body.get("error", "http_error"),
                               body.get("detail", body), path)
            return r
        raise TransportError(f"{method} {path}: gave up after retries")

    def get(self, path: str, **params):
        return self._req("GET", path, retry=True, params=params or None).json()

    def post(self, path: str, body: dict):
        return self._req("POST", path, retry=False, json=body).json()

    def put(self, path: str, body: dict):
        return self._req("PUT", path, retry=False, json=body).json()

    def chunk_text(self, n: int) -> str:
        return self._req("GET", f"/events/chunks/{n}", retry=True).text

    # ------------------------------------------------------------ API
    def state(self) -> dict:
        return self.get("/sim/state")

    def workflows(self) -> list[dict]:
        return self.get("/workflows")

    def workflow(self, wid: str) -> dict:
        return self.get(f"/workflows/{wid}")

    def config(self, wid: str) -> dict:
        return self.get(f"/workflows/{wid}/config")

    def config_history(self, wid: str) -> list[dict]:
        return self.get(f"/workflows/{wid}/config/history")

    def chunks(self, kind: str, after: int = -1) -> list[dict]:
        return self.get("/events/chunks", kind=kind, after=after)

    def approvals(self, workflow_id: str | None = None) -> list[dict]:
        return self.get("/approvals", **({"workflow_id": workflow_id} if workflow_id else {}))

    def approval(self, aid: str) -> dict:
        return self.get(f"/approvals/{aid}")

    def advance(self, minutes: int) -> dict:
        """Advance is not idempotent. On a transport error we read the clock back instead of retrying."""
        before = None
        try:
            before = self.state()["sim_minute"]
            return self.post("/sim/advance", {"minutes": minutes})
        except TransportError:
            st = self.state()
            if before is not None and st["sim_minute"] > before:
                log.warning("advance response lost but the clock moved; continuing from %s", st["ts"])
                return {"sim_minute": st["sim_minute"], "ts": st["ts"], "finished": st["finished"],
                        "new_chunks": []}
            raise
