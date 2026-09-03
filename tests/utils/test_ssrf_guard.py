"""Tests for src.utils.ssrf_guard (Milestone 4 W-A1, gatewayz-backend#2262
-- review fix round 1). Every address-class check is exercised by
monkeypatching socket.getaddrinfo -- no real DNS/network."""

import socket

import pytest

from src.utils.ssrf_guard import (
    SSRFBlockedError,
    assert_public_https_url,
    resolve_public_ip,
)


def _fake_getaddrinfo(*ips):
    """Build a socket.getaddrinfo replacement returning `ips` (a mix of
    IPv4/IPv6 literals), shaped like the real function's return value."""

    def _fake(hostname, port, *args, **kwargs):
        infos = []
        for ip in ips:
            if ":" in ip:
                infos.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0)))
            else:
                infos.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)))
        return infos

    return _fake


def _blocked_addresses_by_getaddrinfo():
    """(label, ip) pairs that must all be rejected."""
    return [
        ("loopback_v4", "127.0.0.1"),
        ("private_10", "10.0.0.5"),
        ("private_172_16", "172.16.0.5"),
        ("private_192_168", "192.168.1.5"),
        ("link_local_v4", "169.254.1.1"),
        ("cloud_metadata", "169.254.169.254"),
        ("cgnat", "100.64.0.1"),
        ("multicast_v4", "224.0.0.1"),
        ("reserved_v4", "240.0.0.1"),
        ("unspecified_v4", "0.0.0.0"),
        ("loopback_v6", "::1"),
        ("unique_local_v6", "fc00::1"),
        ("link_local_v6", "fe80::1"),
        ("ipv4_mapped_metadata", "::ffff:169.254.169.254"),
        ("ipv4_mapped_cgnat", "::ffff:100.64.0.1"),
    ]


@pytest.mark.parametrize("label,ip", _blocked_addresses_by_getaddrinfo())
def test_resolve_public_ip_blocks_non_public_addresses(monkeypatch, label, ip):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(ip))
    with pytest.raises(SSRFBlockedError) as exc_info:
        resolve_public_ip("node.example.com")
    assert exc_info.value.reason == "private_address_blocked", label


def test_resolve_public_ip_allows_a_public_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    assert resolve_public_ip("node.example.com") == "93.184.216.34"


def test_resolve_public_ip_allows_a_public_ipv6_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("2001:4860:4860::8888"))
    assert resolve_public_ip("node.example.com") == "2001:4860:4860::8888"


def test_resolve_public_ip_rejects_mixed_public_and_private_answer(monkeypatch):
    """A DNS answer with a public 'decoy' record alongside an internal one
    must fail closed -- which record an HTTP client connects to isn't
    controlled by us."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34", "127.0.0.1"))
    with pytest.raises(SSRFBlockedError) as exc_info:
        resolve_public_ip("node.example.com")
    assert exc_info.value.reason == "private_address_blocked"


def test_resolve_public_ip_wraps_dns_failure(monkeypatch):
    def _raise(*args, **kwargs):
        raise socket.gaierror("name not known")

    monkeypatch.setattr(socket, "getaddrinfo", _raise)
    with pytest.raises(SSRFBlockedError) as exc_info:
        resolve_public_ip("nonexistent.invalid")
    assert exc_info.value.reason == "dns_resolution_failed"


def test_assert_public_https_url_rejects_non_https_scheme(monkeypatch):
    with pytest.raises(SSRFBlockedError) as exc_info:
        assert_public_https_url("http://node.example.com")
    assert exc_info.value.reason == "scheme_not_https"


def test_assert_public_https_url_rejects_embedded_credentials(monkeypatch):
    with pytest.raises(SSRFBlockedError) as exc_info:
        assert_public_https_url("https://user:pass@node.example.com")
    assert exc_info.value.reason == "embedded_credentials_not_allowed"


def test_assert_public_https_url_rejects_missing_hostname():
    with pytest.raises(SSRFBlockedError) as exc_info:
        assert_public_https_url("https:///path")
    assert exc_info.value.reason == "missing_hostname"


def test_assert_public_https_url_returns_resolved_ip_for_public_host(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    assert assert_public_https_url("https://node.example.com/anything") == "93.184.216.34"


def test_assert_public_https_url_blocks_internal_host(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("10.0.0.1"))
    with pytest.raises(SSRFBlockedError) as exc_info:
        assert_public_https_url("https://internal.example.com")
    assert exc_info.value.reason == "private_address_blocked"
