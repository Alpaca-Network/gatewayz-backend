"""get_display_eth_usd_price must never block the (async) caller on RPC."""

import threading
import time

from src.services.gpu import payout_views


def test_cold_cache_returns_none_without_blocking(monkeypatch):
    release = threading.Event()
    sentinel = object()

    def slow_fetch():
        release.wait(5)
        return sentinel

    monkeypatch.setattr(payout_views, "_fetch_price", slow_fetch)
    monkeypatch.setattr(payout_views, "_price_cache", None)

    start = time.monotonic()
    assert payout_views.get_display_eth_usd_price() is None
    assert time.monotonic() - start < 0.5

    release.set()
    deadline = time.monotonic() + 2
    while payout_views.get_display_eth_usd_price() is not sentinel:
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_expired_cache_serves_last_price_while_refreshing(monkeypatch):
    old, new = object(), object()
    release = threading.Event()
    calls = []

    def fetch():
        calls.append(1)
        release.wait(5)
        return new

    monkeypatch.setattr(payout_views, "_fetch_price", fetch)
    monkeypatch.setattr(payout_views, "_price_cache", (time.monotonic() - 1, old))

    assert payout_views.get_display_eth_usd_price() is old
    assert payout_views.get_display_eth_usd_price() is old
    release.set()
    deadline = time.monotonic() + 2
    while payout_views.get_display_eth_usd_price() is not new:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert len(calls) == 1  # single-flight refresh
