"""Endpoint rate limiter: public routes count per client IP, not per bearer string."""

import secrets

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.services.endpoint_rate_limiter import create_endpoint_rate_limit


def _req(token: str | None = None, ip: str = "203.0.113.7") -> Request:
    headers = [(b"x-real-ip", ip.encode()), (b"x-forwarded-for", ip.encode())]
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return Request({"type": "http", "headers": headers, "client": (ip, 1234)})


@pytest.mark.asyncio
async def test_public_route_limits_per_ip_whatever_the_token():
    rl = create_endpoint_rate_limit(f"t-{secrets.token_hex(4)}", 3, 60, key_by_ip=True)
    for _ in range(3):
        await rl(_req(secrets.token_hex(8)))  # a fresh made-up token each time
    with pytest.raises(HTTPException) as e:
        await rl(_req(secrets.token_hex(8)))
    assert e.value.status_code == 429
    await rl(_req(secrets.token_hex(8), ip="198.51.100.9"))  # another client is unaffected


@pytest.mark.asyncio
async def test_requests_without_a_key_are_limited_too():
    rl = create_endpoint_rate_limit(f"t-{secrets.token_hex(4)}", 2, 60)
    await rl(_req())
    await rl(_req())
    with pytest.raises(HTTPException):
        await rl(_req())


@pytest.mark.asyncio
async def test_keyed_routes_still_count_per_key():
    rl = create_endpoint_rate_limit(f"t-{secrets.token_hex(4)}", 1, 60)
    await rl(_req("key-a"))
    await rl(_req("key-b"))  # same IP, different key: separate bucket
    with pytest.raises(HTTPException):
        await rl(_req("key-a"))
