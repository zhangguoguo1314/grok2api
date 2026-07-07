"""
Proxy pool with sticky selection, failover rotation, and health tracking.

Supports comma-separated or newline-separated proxy URLs in config. Callers
keep using the current proxy until a retry path explicitly rotates to the
next one. Unhealthy proxies are skipped during selection but remain in the
pool for periodic re-testing.
"""

import threading
import time
from typing import Optional

from app.core.logger import logger

# ---- internal state ----
_lock = threading.Lock()
_pools: dict[str, list[str]] = {}       # key -> parsed list
_indexes: dict[str, int] = {}           # key -> current index
_raw_cache: dict[str, str] = {}         # key -> last raw config value
_health: dict[str, dict[str, dict]] = {}  # key -> {proxy_url: {"ok": bool, "ts": float}}

_FAILOVER_STATUS_CODES = frozenset({403, 429, 502})


def _parse_proxies(raw: str) -> list[str]:
    """Parse comma-separated or newline-separated proxy URLs."""
    if not raw:
        return []
    if "\n" in raw:
        parts = raw.split("\n")
    else:
        parts = raw.split(",")
    return [p.strip() for p in parts if p.strip()]


def _ensure_pool(config_key: str) -> list[str]:
    """Load and cache the proxy list for *config_key*."""
    from app.core.config import config  # avoid circular at module level

    raw = config.get(config_key, "") or ""
    if raw != _raw_cache.get(config_key):
        proxies = _parse_proxies(raw)
        with _lock:
            _pools[config_key] = proxies
            _indexes[config_key] = 0
            _raw_cache[config_key] = raw
            # init health records
            if config_key not in _health:
                _health[config_key] = {}
            for p in proxies:
                if p not in _health[config_key]:
                    _health[config_key][p] = {"ok": True, "ts": 0}
            # clean removed
            for p in list(_health[config_key].keys()):
                if p not in proxies:
                    _health[config_key].pop(p, None)
        if len(proxies) > 1:
            logger.info(
                f"ProxyPool: {config_key} loaded {len(proxies)} proxies for failover"
            )
    return _pools.get(config_key, [])


def get_current_proxy(config_key: str) -> str:
    """Return the current sticky proxy URL for *config_key*."""
    with _lock:
        pool = _ensure_pool(config_key)
        if not pool:
            return ""
        health = _health.get(config_key, {})
        healthy = [p for p in pool if health.get(p, {}).get("ok", True)]
        target = healthy if healthy else pool
        idx = _indexes.get(config_key, 0) % len(target)
        _indexes[config_key] = idx
        return target[idx]


def get_current_proxy_from(*config_keys: str) -> tuple[Optional[str], str]:
    """Return the first configured sticky proxy from *config_keys*."""
    for config_key in config_keys:
        proxy = get_current_proxy(config_key)
        if proxy:
            return config_key, proxy
    return None, ""


def rotate_proxy(config_key: str) -> str:
    """Advance *config_key* to the next proxy and return it."""
    with _lock:
        pool = _ensure_pool(config_key)
        if not pool:
            return ""
        if len(pool) == 1:
            return pool[0]
        health = _health.get(config_key, {})
        healthy = [p for p in pool if health.get(p, {}).get("ok", True)]
        target = healthy if healthy else pool
        next_idx = (_indexes.get(config_key, 0) + 1) % len(target)
        _indexes[config_key] = next_idx
        proxy = target[next_idx]
        logger.warning(
            f"ProxyPool: rotate {config_key} to index {next_idx + 1}/{len(target)}"
        )
        return proxy


def should_rotate_proxy(status_code: Optional[int]) -> bool:
    """Return whether *status_code* should trigger proxy failover."""
    return status_code in _FAILOVER_STATUS_CODES


def build_http_proxies(proxy_url: str) -> Optional[dict[str, str]]:
    """Build curl_cffi-style proxies mapping from a single proxy URL."""
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


# ---- health helpers (used by admin API / refresh task) ----

def get_proxy_health(config_key: str = "proxy.base_proxy_url") -> list[dict]:
    """Return all proxies with health status for admin UI."""
    with _lock:
        pool = _ensure_pool(config_key)
        health = _health.get(config_key, {})
        return [
            {
                "url": p,
                "ok": health.get(p, {}).get("ok", True),
                "ts": health.get(p, {}).get("ts", 0),
            }
            for p in pool
        ]


def set_proxy_health(config_key: str, proxy_url: str, ok: bool) -> None:
    """Mark a proxy as healthy/unhealthy."""
    with _lock:
        if config_key not in _health:
            _health[config_key] = {}
        _health[config_key][proxy_url] = {"ok": ok, "ts": time.time()}


def set_proxies_list(config_key: str, proxy_list: list[str]) -> None:
    """Set proxy list programmatically (e.g. from admin UI)."""
    proxies = [p.strip() for p in proxy_list if p.strip()]
    with _lock:
        _pools[config_key] = proxies
        _indexes[config_key] = 0
        _raw_cache[config_key] = ",".join(proxies)
        if config_key not in _health:
            _health[config_key] = {}
        for p in proxies:
            if p not in _health[config_key]:
                _health[config_key][p] = {"ok": True, "ts": 0}
        for p in list(_health[config_key].keys()):
            if p not in proxies:
                _health[config_key].pop(p, None)
    logger.info(f"ProxyPool: {config_key} set {len(proxies)} proxies")


__all__ = [
    "build_http_proxies",
    "get_current_proxy",
    "get_current_proxy_from",
    "rotate_proxy",
    "should_rotate_proxy",
    "get_proxy_health",
    "set_proxy_health",
    "set_proxies_list",
]
