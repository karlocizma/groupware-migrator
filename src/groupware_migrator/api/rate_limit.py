from __future__ import annotations

import threading
import time

from fastapi import HTTPException


class LoginRateLimiter:
    """In-memory sliding-window rate limiter for login attempts keyed by IP."""

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 300,
        lockout_seconds: int = 900,
    ):
        self._max = max_attempts
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._attempts: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def check_and_record(self, ip: str) -> None:
        """Record an attempt and raise HTTP 429 if the IP is rate-limited."""
        now = time.monotonic()
        with self._lock:
            locked_until = self._locked_until.get(ip, 0.0)
            if now < locked_until:
                remaining = int(locked_until - now)
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many login attempts. Try again in {remaining} seconds.",
                )
            cutoff = now - self._window
            self._attempts[ip] = [t for t in self._attempts.get(ip, []) if t > cutoff]
            self._attempts[ip].append(now)
            if len(self._attempts[ip]) > self._max:
                self._locked_until[ip] = now + self._lockout
                del self._attempts[ip]
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many login attempts. Try again in {self._lockout} seconds.",
                )

    def clear(self, ip: str) -> None:
        """Clear attempts for an IP after a successful login."""
        with self._lock:
            self._attempts.pop(ip, None)
            self._locked_until.pop(ip, None)
