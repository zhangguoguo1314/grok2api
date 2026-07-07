"""Admin proxy pool management API."""

import asyncio
import time
import urllib.request
import ssl

from fastapi import APIRouter, Depends, Request

from app.core.auth import verify_app_key
from app.core.config import config
from app.core.logger import logger
from app.core.proxy_pool import get_proxy_health, set_proxies_list, set_proxy_health

router = APIRouter(tags=["admin"], dependencies=[Depends(verify_app_key)])


async def _test_proxy(proxy_url: str):
    """Test a single proxy by connecting to grok.com through it."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    start = time.time()
    try:
        req = urllib.request.Request(
            "https://grok.com",
            headers={"User-Agent": "Mozilla/5.0"},
            method="HEAD",
        )
        parsed = proxy_url.split("://")
        if len(parsed) >= 2:
            scheme = parsed[0]
            rest = parsed[1] if len(parsed) == 2 else "://".join(parsed[1:])
            if scheme in ("http", "https"):
                req.set_proxy(rest, scheme)
            else:
                import socket
                host_port = rest.split("@")[-1]
                host, port_str = host_port.rsplit(":", 1)
                port = int(port_str)
                sock = socket.create_connection((host, port), timeout=8)
                sock.close()
                return True, time.time() - start, ""

        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            elapsed = time.time() - start
            if resp.status < 400:
                return True, elapsed, ""
            return False, elapsed, f"HTTP {resp.status}"
    except Exception as e:
        return False, None, str(e)[:200]


@router.get("/v1/admin/proxies")
async def list_proxies():
    """List all proxies with health status."""
    proxies = get_proxy_health("proxy.base_proxy_url")
    return {
        "proxies": proxies,
        "healthy_count": sum(1 for p in proxies if p["ok"]),
        "unhealthy_count": sum(1 for p in proxies if not p["ok"]),
    }


@router.post("/v1/admin/proxies")
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


@router.post("/v1/admin/proxies/refresh")
async def refresh_proxy_pool():
    """Refresh all proxy health status."""
    proxies = get_proxy_health("proxy.base_proxy_url")
    if not proxies:
        return {"success": True, "total": 0, "healthy": 0, "unhealthy": 0, "results": {}}

    tasks = [_test_proxy(p["url"]) for p in proxies]
    checked = await asyncio.gather(*tasks, return_exceptions=True)

    results = {}
    for i, p in enumerate(proxies):
        result = checked[i]
        if isinstance(result, Exception):
            healthy, latency, error = False, None, str(result)[:200]
        else:
            healthy, latency, error = result

        set_proxy_health("proxy.base_proxy_url", p["url"], healthy)
        results[p["url"]] = {"ok": healthy, "latency": latency, "error": error}

    healthy_count = sum(1 for r in results.values() if r["ok"])
    logger.info(f"ProxyPool refresh: {healthy_count}/{len(proxies)} healthy")
    return {
        "success": True,
        "total": len(proxies),
        "healthy": healthy_count,
        "unhealthy": len(proxies) - healthy_count,
        "results": results,
    }


@router.delete("/v1/admin/proxies")
async def clear_proxies():
    """Clear all proxies."""
    await config.update({"proxy": {"base_proxy_url": ""}})
    set_proxies_list("proxy.base_proxy_url", [])
    logger.info("Admin cleared all proxies")
    return {"success": True, "message": "All proxies cleared"}
