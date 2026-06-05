"""Generic Redis read-cache decorator and write helper.

Usage
-----
Apply :func:`redis_cached` to any method that returns ``T | None`` to get
transparent cache-aside behaviour.  Call :func:`cache_set` from write paths
for write-through updates.

Example::

    @cache.redis_cached(
        key_function=lambda resource_type, rid: f"res:{resource_type}:{rid}",
        ttl=FHIRStore.TTL.LATEST,
        serialize=my_obj_to_json,
        deserialize=json_to_my_obj,
    )
    def read_something(self, resource_type: str, rid: str) -> MyObj | None:
        ...  # only called on a cache miss

    # after a write:
    cache.cache_set(key, new_value, FHIRStore.TTL.LATEST, my_obj_to_json)
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any, TypeVar, cast

import redis

import app.config as config

logger = logging.getLogger("fhir")

_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(config.REDIS_URL, decode_responses=True)
    return _client


def _safe_get(key: str) -> str | None:
    try:
        return cast(str | None, _get_client().get(key))
    except redis.RedisError as exc:
        logger.warning("cache get failed: %s", exc)
        return None


def _safe_set(key: str, value: str, ttl: int) -> None:
    try:
        _get_client().set(key, value, ex=ttl)
    except redis.RedisError as exc:
        logger.warning("cache set failed: %s", exc)


T = TypeVar("T")


def cache_set(key: str, value: Any, ttl: int, serialize: Callable[[Any], str]) -> None:
    """Store a value in Redis using the provided serializer."""
    _safe_set(key, serialize(value), ttl)


def cache_delete(key: str) -> None:
    """Remove a key from Redis. Silently does nothing if the key does not exist."""
    try:
        _get_client().delete(key)
    except redis.RedisError as exc:
        logger.warning("cache delete failed: %s", exc)


def redis_cached(
    key_function: Callable[..., str],
    ttl: int,
    serialize: Callable[[Any], str],
    deserialize: Callable[[str], Any],
    is_method: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Cache-aside decorator for functions or instance methods returning ``T | None``.

    ``key_function`` receives the positional arguments used to derive the cache
    key.  Set ``is_method=True`` (the default) to skip ``self``; set
    ``is_method=False`` for standalone functions where all arguments matter.

    ``serialize`` / ``deserialize`` convert the return value to/from a string
    for Redis storage.

    Examples::

        # Instance method — self is skipped automatically
        @cache.redis_cached(
            key_function=lambda resource_type, resource_id: f"fhir:v:{resource_type}:{resource_id}",
            ttl=FHIRStore.TTL.LATEST,
            serialize=_resource_version_to_json,
            deserialize=_json_to_resource_version,
        )
        def latest(self, resource_type: str, resource_id: str) -> ResourceVersion | None: ...

        # Standalone function — pass is_method=False
        @cache.redis_cached(
            key_function=lambda n: f"fib:{n}",
            ttl=3600,
            serialize=str,
            deserialize=int,
            is_method=False,
        )
        def fibonacci(n: int) -> int: ...
    """
    _skip = 1 if is_method else 0

    def decorator(method: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(method)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = key_function(*args[_skip:], **kwargs)
            raw = _safe_get(key)
            if raw is not None:
                return deserialize(raw)
            result = method(*args, **kwargs)
            if result is not None:
                _safe_set(key, serialize(result), ttl)
            return result

        return wrapper

    return decorator


def write_through(
    key_function: Callable[..., str],
    ttl: int,
    serialize: Callable[[Any], str],
    result_function: Callable[[Any], Any] | None = None,
    is_method: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that caches a write method's return value only on success.

    Mirrors :func:`redis_cached` for the write path: the wrapped method is
    called normally; if it returns without raising, the result is serialised
    and stored in Redis.

    ``key_function`` receives the cacheable value first, then the method's non-self
    positional arguments — consistent with :func:`redis_cached`'s ``key_function``
    convention so both decorators read the same way.

    ``result_function`` optionally extracts the cacheable part from the full return
    value (e.g. ``result_function=lambda r: r[0]`` when the method returns a
    ``(ResourceVersion, bool)`` tuple).

    Example::

        @cache.write_through(
            key_function=lambda rv, resource_type, resource_id, *_:
                f"fhir:v:{resource_type}:{resource_id}",
            ttl=FHIRStore.TTL.LATEST,
            serialize=_rv_to_json,
            result_function=lambda r: r[0],   # unwrap (rv, created) tuple
        )
        def update(self, resource_type, resource_id, ...) -> tuple[ResourceVersion, bool]:
            ...  # pure DB logic; cache is handled by the decorator
    """
    # Pre-compute how many positional args key_function declares so we only pass
    # what it needs and avoid unused-parameter lint warnings at call sites.
    # Pre-compute arity so we only pass the positional args each key_function
    # actually declares.  value is appended last so lambdas that only
    # need method args (e.g. resource_type, resource_id) never receive
    # it and never need an unused placeholder parameter.
    _key_function_arity = len(inspect.signature(key_function).parameters)
    _skip = 1 if is_method else 0

    def decorator(method: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(method)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = method(*args, **kwargs)
            value = result_function(result) if result_function else result
            if value is not None:
                call_args = (value, *args[_skip:])[:_key_function_arity]
                key = key_function(*call_args)
                _safe_set(key, serialize(value), ttl)
            return result

        return wrapper

    return decorator
