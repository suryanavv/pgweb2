"""Bounded in-process login throttling for the single-worker API deployment."""

from collections import OrderedDict, deque
from hashlib import sha256
from time import monotonic

from app.core.config import get_settings


class LoginRateLimiter:
    def __init__(self) -> None:
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()
        self._pending: dict[str, int] = {}

    @staticmethod
    def _key(value: str) -> str:
        return sha256(value.encode("utf-8", "replace")).hexdigest()

    def reserve(self, ip: str, email: str) -> bool:
        now = monotonic()
        settings = get_settings()
        window = max(1, settings.login_attempt_window_seconds)
        ip_key, pair_key = self._keys(ip, email)
        keys = (ip_key, pair_key)
        for key in keys:
            self._prune_key(key, now, window)
        if (
            len(self._failures.get(ip_key, ())) + self._pending.get(ip_key, 0) >= max(1, settings.max_login_failures_per_ip)
            or len(self._failures.get(pair_key, ())) + self._pending.get(pair_key, 0) >= max(1, settings.max_login_failures_per_ip_email)
        ):
            return False

        limit = max(2, settings.max_login_rate_entries)
        all_keys = set(self._failures) | set(self._pending) | set(keys)
        while len(all_keys) > limit:
            evict = next((key for key in self._failures if key not in keys and not self._pending.get(key)), None)
            if evict is None:
                return False
            self._failures.pop(evict, None)
            all_keys.discard(evict)
        for key in keys:
            self._pending[key] = self._pending.get(key, 0) + 1
        return True

    def finish_failure(self, ip: str, email: str) -> None:
        now = monotonic()
        settings = get_settings()
        keys = self._keys(ip, email)
        self._release(keys)
        for key in keys:
            self._prune_key(key, now, max(1, settings.login_attempt_window_seconds))
            self._failures.setdefault(key, deque()).append(now)
            self._failures.move_to_end(key)

    def finish_success(self, ip: str, email: str) -> None:
        keys = self._keys(ip, email)
        self._release(keys)
        self._failures.pop(keys[1], None)

    def release(self, ip: str, email: str) -> None:
        """Release a reservation when authentication fails before an outcome is known."""
        self._release(self._keys(ip, email))

    def clear(self) -> None:
        """Clear volatile counters for isolated tests and orderly lifecycle cleanup."""
        self._failures.clear()
        self._pending.clear()

    def _keys(self, ip: str, email: str) -> tuple[str, str]:
        return f"ip:{self._key(ip)}", f"pair:{self._key(ip + chr(0) + email)}"

    def _release(self, keys: tuple[str, str]) -> None:
        for key in keys:
            pending = self._pending.get(key, 0)
            if pending <= 1:
                self._pending.pop(key, None)
            else:
                self._pending[key] = pending - 1

    def _prune_key(self, key: str, now: float, window: int) -> None:
        bucket = self._failures.get(key)
        if bucket is None:
            return
        cutoff = now - window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if not bucket:
            self._failures.pop(key, None)
        else:
            self._failures.move_to_end(key)


login_limiter = LoginRateLimiter()
