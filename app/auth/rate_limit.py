"""Redis-backed login rate limiter.

Tracks failed login attempts per username and enforces a timed lockout.
Uses two keys per username:
  fhir:login:attempts:{username}  — failure counter (expires with the lockout)
  fhir:login:locked:{username}    — absolute expiry timestamp as a float

To swap in a different backend (e.g. Valkey, Memcached) replace _get_client()
and the five public functions; call sites in the router stay unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import redis

import app.config as config

MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

_KEY_ATTEMPTS = "fhir:login:attempts:{}"
_KEY_LOCKED = "fhir:login:locked:{}"

# Module-level client — replaced with a FakeRedis instance in unit tests.
_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(config.REDIS_URL, decode_responses=True)
    return _client


def locked_until(username: str) -> datetime | None:
    """Return the lockout expiry if the account is currently locked, else None."""
    val = _get_client().get(_KEY_LOCKED.format(username))
    if val is None:
        return None
    return datetime.fromtimestamp(float(cast(str, val)), tz=UTC)


def record_failure(username: str) -> None:
    """Increment the failure counter; lock the account when the limit is hit."""
    r = _get_client()
    key_attempts = _KEY_ATTEMPTS.format(username)
    key_locked = _KEY_LOCKED.format(username)
    lockout_seconds = LOCKOUT_MINUTES * 60

    count = cast(int, r.incr(key_attempts))
    r.expire(key_attempts, lockout_seconds)

    if count >= MAX_ATTEMPTS:
        expiry = datetime.now(UTC) + timedelta(minutes=LOCKOUT_MINUTES)
        r.set(key_locked, expiry.timestamp(), ex=lockout_seconds)


def reset(username: str) -> None:
    """Clear failures and any lockout on successful login."""
    r = _get_client()
    r.delete(_KEY_ATTEMPTS.format(username), _KEY_LOCKED.format(username))


def clear_all() -> None:
    """Flush all rate-limit state. For use between tests only."""
    r = _get_client()
    for key in r.scan_iter("fhir:login:*"):
        r.delete(key)
