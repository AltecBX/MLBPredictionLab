"""Read-through Redis cache.

Keys carry the active model version and a schema revision so a retrain or a
DTO change invalidates cleanly (ARCHITECTURE.md §4).

When Redis is not configured or unreachable the cache degrades to a no-op —
it never returns stale or synthetic data, and never fails a request.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

import redis

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")

CACHE_SCHEMA_REV = "r1"

_client: redis.Redis | None = None
_unavailable = False


def get_client() -> redis.Redis | None:
    global _client, _unavailable
    if _unavailable or not settings.caching_active:
        return None
    if _client is None:
        try:
            _client = redis.Redis.from_url(
                str(settings.redis_url), decode_responses=True, socket_timeout=2
            )
            _client.ping()
        except Exception as exc:  # pragma: no cover - environment dependent
            log.warning("cache.unavailable", error=str(exc))
            _unavailable = True
            _client = None
    return _client


def make_key(namespace: str, *parts: object, model_version: str | int | None = None) -> str:
    tokens = [CACHE_SCHEMA_REV, namespace]
    if model_version is not None:
        tokens.append(f"mv{model_version}")
    tokens.extend(str(p) for p in parts)
    return ":".join(tokens)


def get_json(key: str) -> Any | None:
    client = get_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
    except Exception as exc:  # pragma: no cover
        log.warning("cache.get_failed", key=key, error=str(exc))
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def set_json(key: str, value: Any, ttl_s: int) -> None:
    client = get_client()
    if client is None:
        return
    try:
        client.setex(key, ttl_s, json.dumps(value, default=str))
    except Exception as exc:  # pragma: no cover
        log.warning("cache.set_failed", key=key, error=str(exc))


def cached_json(key: str, ttl_s: int, producer: Callable[[], Any]) -> Any:
    hit = get_json(key)
    if hit is not None:
        return hit
    value = producer()
    set_json(key, value, ttl_s)
    return value


def invalidate_prefix(prefix: str) -> int:
    """Delete keys under a prefix. Returns the number removed."""
    client = get_client()
    if client is None:
        return 0
    removed = 0
    try:
        for key in client.scan_iter(match=f"{prefix}*", count=500):
            client.delete(key)
            removed += 1
    except Exception as exc:  # pragma: no cover
        log.warning("cache.invalidate_failed", prefix=prefix, error=str(exc))
    return removed


def health() -> dict[str, Any]:
    if not settings.caching_active:
        return {"configured": False, "reachable": False, "detail": "Caching disabled."}
    client = get_client()
    if client is None:
        return {"configured": True, "reachable": False, "detail": "Redis unreachable."}
    try:
        info = client.info("server")
        return {
            "configured": True,
            "reachable": True,
            "version": info.get("redis_version"),
        }
    except Exception as exc:  # pragma: no cover
        return {"configured": True, "reachable": False, "detail": str(exc)}
