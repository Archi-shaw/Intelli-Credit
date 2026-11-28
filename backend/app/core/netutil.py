"""Client IP resolution behind trusted reverse proxies (the Next.js frontend proxies all API calls).

``X-Forwarded-For`` is only honoured when the direct peer is a configured trusted proxy, and the
right-most address not belonging to a trusted proxy is used, so clients cannot spoof their IP by
sending their own header.
"""
from __future__ import annotations

import ipaddress
from functools import lru_cache

from starlette.requests import Request

from app.core.config import settings


@lru_cache(maxsize=8)
def _trusted(spec: str) -> tuple:
    nets = []
    for part in spec.split(","):
        part = part.strip()
        if part:
            try:
                nets.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                continue
    return tuple(nets)


def _is_trusted(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in _trusted(settings.TRUSTED_PROXIES))


def client_ip(request: Request) -> str | None:
    if settings.CLIENT_IP_HEADER:  # platform-set header (e.g. Vercel's x-real-ip); the platform overwrites it
        value = request.headers.get(settings.CLIENT_IP_HEADER, "").split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            pass
    peer = request.client.host if request.client else None
    if not peer or not _is_trusted(peer):
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    for hop in reversed(hops):
        if not _is_trusted(hop):
            try:
                ipaddress.ip_address(hop)
            except ValueError:
                return peer
            return hop
    return hops[0] if hops else peer
