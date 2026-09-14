"""Validation for caller-supplied webhook URLs.

A budget webhook takes a URL from a customer and has the gateway fetch it. That
is a server-side request forgery primitive unless it is fenced: without this
check a caller could point us at 169.254.169.254 and have the gateway read its
own cloud metadata, or sweep internal services that are only reachable from
inside the network and read the outcome from timing.

The fence is deliberately allow-list shaped -- https, public addresses, a
default port -- because a deny-list of "bad" hosts is a list you are always one
encoding trick behind.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

MAX_URL_LENGTH = 2048


class InvalidWebhookTarget(ValueError):
    """The URL is not somewhere this gateway will send a request."""


def _resolved_addresses(host: str) -> list[ipaddress._BaseAddress]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise InvalidWebhookTarget(f"host does not resolve: {host}") from e
    out = []
    for info in infos:
        addr = info[4][0]
        try:
            out.append(ipaddress.ip_address(addr.split("%")[0]))
        except ValueError:
            continue
    if not out:
        raise InvalidWebhookTarget(f"host resolves to no usable address: {host}")
    return out


def validate_webhook_url(url: str) -> str:
    """Return the URL if we are willing to call it, else raise.

    Resolution happens here, at registration time, so an obviously-internal
    target is refused with a clear message rather than failing silently later.
    It is NOT a substitute for the same check at send time: DNS can change
    between the two (a rebinding attack is exactly that), so the sender
    re-validates.
    """
    if not url or len(url) > MAX_URL_LENGTH:
        raise InvalidWebhookTarget("url is empty or too long")

    parsed = urlparse(url)
    if parsed.scheme != "https":
        # Plaintext would put a customer's spend figures on the wire.
        raise InvalidWebhookTarget("only https targets are accepted")
    if not parsed.hostname:
        raise InvalidWebhookTarget("url has no host")
    if parsed.port is not None and parsed.port != 443:
        raise InvalidWebhookTarget("only the default https port is accepted")
    if parsed.username or parsed.password:
        raise InvalidWebhookTarget("credentials in the url are not accepted")

    for addr in _resolved_addresses(parsed.hostname):
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local  # 169.254.0.0/16 -- cloud metadata lives here
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            raise InvalidWebhookTarget(
                f"host resolves to a non-public address ({addr}); "
                "webhooks are only delivered to public endpoints"
            )
    return url
