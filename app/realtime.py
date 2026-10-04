"""Real-time push over WebSockets.

The server pushes; clients act through the normal REST endpoints. Every event is built *per
recipient* at delivery time from that person's current role and clearance, so a right that is
revoked or a clearance that is lowered stops delivery immediately, and nobody ever receives
text above their clearance (it arrives as a redaction marker instead).
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import roles as R


@dataclass
class Conn:
    user_id: str
    jti: str
    expires_at: float
    queue: "asyncio.Queue[Dict[str, Any]]" = field(default_factory=asyncio.Queue)


class Hub:
    def __init__(self, load_user: Callable[[str], Optional[R.User]]) -> None:
        self._load_user = load_user
        self._conns: List[Conn] = []
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ---- connection lifecycle (called from the async WebSocket handler)
    def register(self, user_id: str, jti: str, expires_at: float) -> Conn:
        self._loop = asyncio.get_running_loop()
        conn = Conn(user_id, jti, expires_at)
        with self._lock:
            self._conns.append(conn)
        return conn

    def unregister(self, conn: Conn) -> None:
        with self._lock:
            if conn in self._conns:
                self._conns.remove(conn)

    def count(self) -> int:
        with self._lock:
            return len(self._conns)

    # ---- publishing (called from ordinary threads)
    def publish(self, build: Callable[[R.User], Optional[Dict[str, Any]]]) -> int:
        """``build(user)`` returns the message that user may see, or None. Returns deliveries."""
        with self._lock:
            conns = list(self._conns)
        loop, sent = self._loop, 0
        if loop is None:
            return 0
        users: Dict[str, Optional[R.User]] = {}
        for c in conns:
            if c.user_id not in users:
                users[c.user_id] = self._load_user(c.user_id)
            user = users[c.user_id]
            if user is None:
                continue
            msg = build(user)
            if msg is not None:
                loop.call_soon_threadsafe(c.queue.put_nowait, msg)
                sent += 1
        return sent
