"""SSRF protection for outbound requests to operator-supplied endpoints
(Milestone 4 W-A1, gatewayz-backend#2262 -- review fix round 1).

Community GPU node registration/patch accepts an arbitrary `endpoint_url`
from the caller, and Gatewayz's own server then makes an HTTP request to
it (src/services/gpu/node_probe.py) -- a classic SSRF vector without this
guard: an attacker points `endpoint_url` at an internal service (cloud
metadata endpoint, admin panel, etc.) and reads the result through the
probe's success/failure signal, or exploits a DNS-rebind window between
this check and the actual connection.

`assert_public_https_url()` is the single call site every outbound fetch
to a community-node-supplied URL must go through. It resolves the
hostname itself and returns the IP the caller should connect to directly
(rather than letting the HTTP client re-resolve DNS at connect time,
which reopens the rebind window this function exists to close).
"""

import ipaddress
import socket
from urllib.parse import urlsplit

# 100.64.0.0/10 -- Carrier-Grade NAT (RFC 6598). ipaddress doesn't classify
# this as private/reserved, so it needs an explicit check.
_CGNAT_RANGE = ipaddress.ip_network("100.64.0.0/10")


class SSRFBlockedError(Exception):
    """Raised when a URL fails the public-address check. `reason` is a
    snake_case code callers can map straight to an API error detail."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True iff `ip` is a normal public internet address -- rejects
    loopback, private (RFC1918), link-local (including the
    169.254.169.254 cloud metadata address), multicast, reserved,
    unspecified, and CGNAT ranges. IPv4-mapped IPv6 addresses are
    unwrapped and re-checked as their embedded IPv4 address so
    ::ffff:169.254.169.254-style addresses can't slip past the IPv6
    branch."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_public_ip(ip.ipv4_mapped)

    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
        return False
    if ip.is_reserved or ip.is_unspecified:
        return False
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_RANGE:
        return False

    return True


def resolve_public_ip(hostname: str) -> str:
    """Resolve `hostname` and return ONE public IP literal to connect to.

    Every address the hostname resolves to must be public -- if any
    A/AAAA record points at a non-public address, the whole hostname is
    rejected. Which record an HTTP client actually connects to isn't
    something the caller controls, so a mixed answer set (one public
    "decoy" record alongside an internal one) must fail closed rather
    than being accepted because at least one record looked fine.

    Raises SSRFBlockedError on resolution failure or any non-public
    address.
    """
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise SSRFBlockedError("dns_resolution_failed") from e

    resolved_ips = {info[4][0] for info in addr_infos}
    if not resolved_ips:
        raise SSRFBlockedError("dns_resolution_failed")

    public_ips = []
    for raw_ip in resolved_ips:
        # IPv6 scoped literals ("fe80::1%eth0") carry a zone id ipaddress
        # can't parse -- strip it; scope doesn't change the address class.
        try:
            ip = ipaddress.ip_address(raw_ip.split("%", 1)[0])
        except ValueError as e:
            raise SSRFBlockedError("invalid_resolved_address") from e
        if not _is_public_ip(ip):
            raise SSRFBlockedError("private_address_blocked")
        public_ips.append(raw_ip)

    return public_ips[0]


def assert_public_https_url(url: str) -> str:
    """Validate `url` is a well-formed https URL with no embedded
    credentials, and that its hostname resolves ONLY to public addresses.

    Returns the resolved IP literal -- callers should connect directly to
    it (pinning the IP, with the original hostname sent via SNI/Host
    headers for TLS/vhost correctness) rather than letting the HTTP
    client re-resolve the hostname, which would reopen the DNS-rebind
    window between this check and the actual connection.

    Raises SSRFBlockedError on any violation.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise SSRFBlockedError("scheme_not_https")
    if parts.username or parts.password:
        raise SSRFBlockedError("embedded_credentials_not_allowed")
    if not parts.hostname:
        raise SSRFBlockedError("missing_hostname")

    return resolve_public_ip(parts.hostname)
