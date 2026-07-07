"""In-process fixed-window rate limiter for the auth endpoints (audit E2).

Deliberately dependency-free and single-process: the app runs on a single Fly
machine (``min_machines_running=1``), so an in-memory per-key counter is
sufficient for V1 to blunt online password brute-force. If the deployment ever
scales past one machine, this must move to a shared store (Redis) — that is a
future ADR, not a V1 concern.

Keys are opaque strings the caller composes (e.g. ``"<ip>:<email>"``). The
window is a fixed 60 seconds; the per-window cap comes from
``Settings.auth_rate_limit_per_minute`` and is passed in per check so it can be
tuned (or disabled with 0) without touching this module.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

_WINDOW_SECONDS = 60.0

# Hard cap on distinct live keys. An attacker spraying unique
# ``login:<ip>:<random-email>`` keys would otherwise grow the map without
# bound and OOM the single Fly machine (audit R7). At the cap we first
# sweep out keys whose whole window has expired; if the map is still full
# of genuinely-live keys (a real high-volume attack), new keys fail closed
# (are rate-limited) rather than allocating unbounded memory.
_MAX_KEYS = 50_000


class FixedWindowRateLimiter:
    """Sliding-window counter keyed on an arbitrary string."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _evict_stale(self, cutoff: float) -> None:
        """Drop keys whose entire window has expired. O(n); only run at cap."""
        stale = [k for k, q in self._hits.items() if not q or q[-1] < cutoff]
        for k in stale:
            del self._hits[k]

    def hit_and_check(self, key: str, max_hits: int) -> bool:
        """Record an attempt for ``key`` and report whether it is allowed.

        Returns ``True`` if the attempt is within ``max_hits`` in the trailing
        window (and records it), ``False`` if the key is already at the limit
        (the attempt is NOT recorded, so a rejected attempt doesn't extend the
        lockout). ``max_hits <= 0`` disables the limiter (always allowed).
        """
        if max_hits <= 0:
            return True
        now = time.monotonic()
        cutoff = now - _WINDOW_SECONDS
        # Bound memory before inserting a brand-new key (audit R7).
        if key not in self._hits and len(self._hits) >= _MAX_KEYS:
            self._evict_stale(cutoff)
            if len(self._hits) >= _MAX_KEYS:
                return False
        q = self._hits[key]
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= max_hits:
            return False
        q.append(now)
        return True

    def reset(self) -> None:
        """Clear all recorded hits (used by tests)."""
        self._hits.clear()


# Process-wide singleton shared by the auth routes.
auth_rate_limiter = FixedWindowRateLimiter()
