"""Admin proxy pool management API."""

import asyncio
import socket
import time
import ssl
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, Request

from app.core.auth import verify_app_key
from app.core.config import config
from app.core.logger import logger
from app.core.proxy_pool import get_proxy_health, set_proxies_list, set_proxy_health

router = APIRouter(tags=["admin"], dependencies=[Depends(verify_app_key)])

# Thread pool for blocking socket operations
_executor = ThreadPoolExecutor(max_workers=20)

# Test target - neutral site to avoid Cloudflare false positives
_TEST_TARGET = "httpbin.org"
_TEST_PORT = 443
_TEST_TIMEOUT = 6  # seconds per proxy


def _tcp_test(host: str, port: int, timeout: float) -> tuple[bool, float, str]:
    """Blocking TCP connectivity test. Runs in thread pool."""
    start = time.time()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        elapsed = time.time() - start
        sock.close()
        return True, elapsed, ""
    except Exception as e:
        return False, time.time() - start, str(e)[:100]


def _http_test(proxy_url: str, timeout: float) -> tuple[bool, float, str]:
    """Test HTTP/HTTPS proxy by fetching httpbin.org/ip through it. Blocking."""
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    start = time.time()
    try:
        req = urllib.request.Request(
            f"https://{_TEST_TARGET}/ip",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            method="GET",
        )
        parsed = proxy_url.split("://")
        if len(parsed) >= 2:
            scheme = parsed[0]
            rest = parsed[1] if len(parsed) == 2 else "://".join(parsed[1:])
            req.set_proxy(rest, scheme)

        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            elapsed = time.time() - start
            if resp.status < 400:
                return True, elapsed, ""
            return False, elapsed, f"HTTP {resp.status}"
    except Exception as e:
        return False, time.time() - start, str(e)[:100]


def _test_proxy_sync(proxy_url: str) -> tuple[bool, float, str]:
    """Synchronous proxy test (runs in thread pool)."""
    start = time.time()
    parsed = proxy_url.split("://")
    if len(parsed) < 2:
        return False, 0, "Invalid URL"

    scheme = parsed[0].lower()
    rest = parsed[1] if len(parsed) == 2 else "://".join(parsed[1:])

    # Extract host:port (handle auth)
    host_port = rest.split("@")[-1]
    if ":" not in host_port:
        return False, 0, "No port specified"

    host = host_port.rsplit(":", 1)[0]
    port_str = host_port.rsplit(":", 1)[1]
    try:
        port = int(port_str)
    except ValueError:
        return False, 0, "Invalid port"

    # Step 1: TCP connectivity test
    ok, elapsed, err = _tcp_test(host, port, _TEST_TIMEOUT)
    if not ok:
        return False, elapsed, f"TCP: {err}"

    # Step 2: For HTTP/HTTPS proxies, also test HTTP through proxy
    if scheme in ("http", "https"):
        ok, elapsed, err = _http_test(proxy_url, _TEST_TIMEOUT)
        return ok, elapsed, err

    # SOCKS proxies: TCP test is sufficient
    return True, time.time() - start, ""


async def _test_proxy_async(proxy_url: str) -> tuple[bool, float, str]:
    """Async wrapper for sync proxy test."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _test_proxy_sync, proxy_url)


@router.get("/proxies")
async def list_proxies():
    """List all proxies with health status."""
    proxies = get_proxy_health("proxy.base_proxy_url")
    return {
        "proxies": proxies,
        "healthy_count": sum(1 for p in proxies if p["ok"]),
        "unhealthy_count": sum(1 for p in proxies if not p["ok"]),
    }


@router.post("/proxies")
async def import_proxies(request: Request):
    """Import proxy list (newline or comma separated)."""
    body = await request.body()
    text = body.decode("utf-8", errors="replace")

    raw_list = text.split("\n") if "\n" in text else text.split(",")
    proxies = [line.strip() for line in raw_list if line.strip() and not line.strip().startswith("#")]

    if not proxies:
        return {"success": False, "message": "No valid proxy URLs found"}

    proxy_str = ",".join(proxies)
    await config.update({"proxy": {"base_proxy_url": proxy_str}})
    set_proxies_list("proxy.base_proxy_url", proxies)

    logger.info(f"Admin imported {len(proxies)} proxies")
    return {"success": True, "imported": len(proxies), "proxies": proxies}


@router.post("/proxies/refresh")
async def refresh_proxy_pool():
    """Refresh all proxy health status with concurrency control."""
    proxies = get_proxy_health("proxy.base_proxy_url")
    if not proxies:
        return {"success": True, "total": 0, "healthy": 0, "unhealthy": 0, "results": {}}

    # Limit concurrency to avoid overwhelming HF Space resources
    semaphore = asyncio.Semaphore(5)

    async def _limited_test(proxy_info: dict) -> tuple[str, bool, float, str]:
        async with semaphore:
            result = await _test_proxy_async(proxy_info["url"])
            return proxy_info["url"], result[0], result[1], result[2]

    tasks = [_limited_test(p) for p in proxies]
    checked = await asyncio.gather(*tasks, return_exceptions=True)

    results = {}
    for item in checked:
        if isinstance(item, Exception):
            continue
        url, ok, latency, error = item
        set_proxy_health("proxy.base_proxy_url", url, ok)
        results[url] = {"ok": ok, "latency": latency, "error": error}

    healthy_count = sum(1 for r in results.values() if r["ok"])
    logger.info(f"ProxyPool refresh: {healthy_count}/{len(proxies)} healthy")
    return {
        "success": True,
        "total": len(proxies),
        "healthy": healthy_count,
        "unhealthy": len(proxies) - healthy_count,
        "results": results,
    }


@router.delete("/proxies")
async def clear_proxies():
    """Clear all proxies."""
    await config.update({"proxy": {"base_proxy_url": ""}})
    set_proxies_list("proxy.base_proxy_url", [])
    logger.info("Admin cleared all proxies")
    return {"success": True, "message": "All proxies cleared"}
