"""Per-client request limits for the login and chat routes (docs/instructions17.md).

Design choices (labelled; the owner may change them):
- Mechanism: an in-memory sliding window per (route group, client address),
  inside this one process. Nothing is stored on disk; a restart starts every
  count from zero. Correct only while the app runs as ONE process (the compose
  files start one uvicorn worker); several workers or replicas would each count
  separately and need a shared store (for example Redis) instead.
- Limits: login 10 attempts per 10 minutes per client; chat 30 turns per
  minute per client. Every attempt counts, successful or not.
- Fail closed: when the table of tracked clients is full, a new client is
  refused (429) rather than let through uncounted.

Client address. The service runs behind a reverse proxy, so the TCP peer is
usually the proxy, not the user:
- TRUSTED_PROXIES (comma-separated IPs or CIDR ranges) lists the proxies whose
  X-Forwarded-For header is believed. Unset or empty -> no proxy is trusted.
- Peer NOT a trusted proxy -> the peer address is the client; any
  X-Forwarded-For it sent is ignored (a client can't pick its own identity).
- Peer IS a trusted proxy -> the client is the right-most X-Forwarded-For
  entry that is not itself a trusted proxy. No such entry, or an entry that
  isn't an IP address -> the request is refused (400): the proxy's own address
  is never counted as the user.
- An invalid TRUSTED_PROXIES value stops the app at startup (fail closed).
uvicorn must not rewrite the peer from X-Forwarded-For itself: the server
profile runs it with --no-proxy-headers.
"""

import ipaddress
import os
import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import Request

MAX_TRACKED_CLIENTS = 10_000


@dataclass(frozen=True)
class Limit:
    requests: int
    window_seconds: float


LIMITS = {
    "login": Limit(10, 600.0),
    "chat": Limit(30, 60.0),
}


class UntrustedClientAddress(Exception):
    """A trusted proxy's request carried no usable client address."""


def parse_trusted_proxies(raw: str | None) -> tuple:
    """'10.0.0.1, 172.16.0.0/12' -> networks. Raises ValueError on a bad entry."""
    networks = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part:
            networks.append(ipaddress.ip_network(part, strict=False))
    return tuple(networks)


TRUSTED_PROXIES = parse_trusted_proxies(os.environ.get("TRUSTED_PROXIES"))


def _ip(value: str):
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _is_trusted(address, proxies) -> bool:
    return address is not None and any(address in network for network in proxies)


def client_address(request: Request, proxies: tuple | None = None) -> str:
    """The address to count this request under. Raises UntrustedClientAddress."""
    proxies = TRUSTED_PROXIES if proxies is None else proxies
    peer = request.client.host if request.client else ""
    if not _is_trusted(_ip(peer), proxies):
        return peer or "unknown"
    entries = [entry.strip()
               for header in request.headers.getlist("x-forwarded-for")
               for entry in header.split(",")]
    for entry in reversed(entries):
        address = _ip(entry)
        if address is None:
            raise UntrustedClientAddress("X-Forwarded-For entry is not an IP address")
        if not _is_trusted(address, proxies):
            return str(address)
    raise UntrustedClientAddress("no client address from the trusted proxy")


class RateLimiter:
    def __init__(self, limits: dict[str, Limit], max_clients: int = MAX_TRACKED_CLIENTS, clock=time.monotonic):
        self.limits = limits
        self.max_clients = max_clients
        self.clock = clock
        self._hits: dict[tuple[str, str], deque] = {}
        self._lock = threading.Lock()

    def allow(self, group: str, client: str) -> tuple[bool, int]:
        """Count one request. Returns (allowed, seconds until the next one is allowed)."""
        limit = self.limits[group]
        now = self.clock()
        key = (group, client)
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= self.max_clients:
                    self._prune(now)
                if len(self._hits) >= self.max_clients:
                    return False, int(limit.window_seconds)
                hits = self._hits[key] = deque()
            while hits and hits[0] <= now - limit.window_seconds:
                hits.popleft()
            if len(hits) >= limit.requests:
                return False, max(1, int(hits[0] + limit.window_seconds - now) + 1)
            hits.append(now)
            return True, 0

    def _prune(self, now: float) -> None:
        for key in [key for key, hits in self._hits.items()
                    if not hits or hits[-1] <= now - self.limits[key[0]].window_seconds]:
            del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter(LIMITS)


def check(request: Request, group: str) -> tuple[str, int] | None:
    """None if the request may go on; otherwise (status, retry_after_seconds)
    with status "rate_limited" (429) or "bad_client_address" (400)."""
    try:
        client = client_address(request)
    except UntrustedClientAddress:
        return "bad_client_address", 0
    allowed, retry_after = limiter.allow(group, client)
    return None if allowed else ("rate_limited", retry_after)
