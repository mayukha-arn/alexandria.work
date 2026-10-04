"""Azure Application Insights telemetry, with no SDK and no content.

Only operational numbers leave the machine: request timings and status codes (by route *template*, never
the real path), answer latency / first-word time / token counts, vector-search time, Solana confirmation
time, and the *names* of security events. Never message text, document text, questions, usernames or ids:
property values are restricted to short strings and numbers, and anything else is dropped.

Telemetry can never slow the app or break it: calls only enqueue, a background thread sends batches, a full
queue drops the oldest data, and every failure is swallowed.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import requests

log = logging.getLogger("alexandria.telemetry")

# Security-relevant events worth alerting on. The event NAME and the department go to Azure; nothing else.
SECURITY_EVENTS = frozenset({
    "AUTH_ACCOUNT_LOCKED", "AUTH_2FA_SETUP_RESTARTED", "GUARDRAIL_BLOCKED", "TAMPER_DETECTED",
    "INJECTION_QUARANTINED", "ACCOUNT_FLAGGED", "SOURCE_FLAGGED", "DOCUMENT_REJECTED", "DOCUMENT_PURGED",
    "WALLET_RESET", "ACCESS_CLEARANCE_CHANGED", "ACCESS_RIGHT_GRANTED", "ACCESS_RIGHT_REVOKED",
})
MAX_STR = 64


def parse_connection_string(cs: str) -> Dict[str, str]:
    return dict(part.split("=", 1) for part in cs.split(";") if "=" in part)


def _clean(props: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Keep only short strings, numbers and booleans, as strings. Everything else is dropped."""
    out: Dict[str, str] = {}
    for k, v in (props or {}).items():
        if isinstance(v, bool) or isinstance(v, (int, float)):
            out[str(k)] = str(v)
        elif isinstance(v, str) and len(v) <= MAX_STR and "@" not in v:
            out[str(k)] = v
    return out


def _duration(ms: float) -> str:
    s, ms_part = divmod(max(0.0, ms), 1000.0)
    m, s = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{int(ms_part):03d}"


class Telemetry:
    def __init__(self, connection_string: Optional[str] = None, role: str = "alexandria-api",
                 post: Optional[Callable[..., Any]] = None, flush_every: float = 5.0, max_queue: int = 2000) -> None:
        cs = parse_connection_string(connection_string) if connection_string else {}
        self.ikey = cs.get("InstrumentationKey")
        endpoint = (cs.get("IngestionEndpoint") or "https://dc.services.visualstudio.com/").rstrip("/")
        self.url = f"{endpoint}/v2.1/track"
        self.enabled = bool(self.ikey)
        self.role, self._post = role, post or requests.post
        self._q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._warned = False
        self.dropped = 0
        if self.enabled:
            self._thread = threading.Thread(target=self._run, args=(flush_every,), name="telemetry", daemon=True)
            self._thread.start()

    # ------------------------------------------------------------------ recording
    def _envelope(self, kind: str, base_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return {"name": f"Microsoft.ApplicationInsights.{(self.ikey or '').replace('-', '')}.{kind}",
                "time": datetime.now(timezone.utc).isoformat(), "iKey": self.ikey,
                "tags": {"ai.cloud.role": self.role}, "data": {"baseType": base_type, "baseData": {"ver": 2, **data}}}

    def _enqueue(self, env: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            self._q.put_nowait(env)
        except queue.Full:
            try:
                self._q.get_nowait()           # drop the oldest rather than block or grow
                self._q.put_nowait(env)
            except Exception:
                pass
            self.dropped += 1

    def request(self, name: str, duration_ms: float, status: int, properties: Optional[Dict[str, Any]] = None) -> None:
        self._enqueue(self._envelope("Request", "RequestData", {
            "id": uuid.uuid4().hex, "name": name, "duration": _duration(duration_ms), "success": status < 500,
            "responseCode": str(status), "properties": _clean(properties)}))

    def metric(self, name: str, value: float, properties: Optional[Dict[str, Any]] = None) -> None:
        self._enqueue(self._envelope("Metric", "MetricData", {
            "metrics": [{"name": name, "value": float(value), "count": 1}], "properties": _clean(properties)}))

    def event(self, name: str, properties: Optional[Dict[str, Any]] = None, measurements: Optional[Dict[str, float]] = None) -> None:
        self._enqueue(self._envelope("Event", "EventData", {
            "name": name, "properties": _clean(properties),
            "measurements": {str(k): float(v) for k, v in (measurements or {}).items()}}))

    # ------------------------------------------------------------------- sending
    def flush(self) -> int:
        batch: List[Dict[str, Any]] = []
        while len(batch) < 100:
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                break
        if not batch:
            return 0
        try:
            r = self._post(self.url, data=json.dumps(batch), headers={"Content-Type": "application/json"}, timeout=10)
            if getattr(r, "status_code", 200) >= 400 and not self._warned:
                self._warned = True
                log.warning("Application Insights rejected telemetry: HTTP %s", r.status_code)
        except Exception as exc:               # never let telemetry hurt the app
            if not self._warned:
                self._warned = True
                log.warning("Application Insights unreachable (%s); telemetry is being dropped", exc)
        return len(batch)

    def _run(self, every: float) -> None:
        while not self._stop.wait(every):
            while self.flush() == 100:
                pass

    def close(self) -> None:
        self._stop.set()
        while self.flush():
            pass
