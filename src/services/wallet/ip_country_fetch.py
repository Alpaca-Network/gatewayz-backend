"""Keep the IP country database file up to date on the server.

The country check for wallet linking (src/services/wallet/ip_country.py)
reads a local MMDB file at Config.WALLET_LINK_IP_COUNTRY_DB_PATH. When
Config.WALLET_LINK_IP_COUNTRY_DB_URL is also set, this module downloads the
file at startup and then checks once a day, replacing it when it is older
than MAX_AGE_DAYS. The URL may contain ``{month}`` (YYYY-MM): the current
month is tried first, then the previous one (a new month's file appears a
few days in). See docs/wallet/ip-country.md.

A download replaces the file only after it parses as an MMDB country
database and answers a known address, so a bad download never removes a
working file. Errors are logged; nothing here raises to the caller.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import logging
import os
import tempfile
import time
from datetime import UTC, datetime

import httpx

from src.config.config import Config
from src.services.wallet.ip_country import MMDBError, MMDBReader

logger = logging.getLogger(__name__)

MAX_AGE_DAYS = 30
CHECK_INTERVAL_SECONDS = 24 * 3600
DOWNLOAD_TIMEOUT_SECONDS = 120
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024
# A well-known address whose country is stable, used to check a download.
_CHECK_IP, _CHECK_COUNTRY = "8.8.8.8", "US"


def fetch_configured() -> tuple[str, str]:
    url = (getattr(Config, "WALLET_LINK_IP_COUNTRY_DB_URL", "") or "").strip()
    path = (getattr(Config, "WALLET_LINK_IP_COUNTRY_DB_PATH", "") or "").strip()
    return url, path


def candidate_urls(template: str, now: datetime | None = None) -> list[str]:
    """The URLs to try, in order: this month, then last month."""
    if "{month}" not in template:
        return [template]
    now = now or datetime.now(UTC)
    year, month = now.year, now.month
    previous = (year - 1, 12) if month == 1 else (year, month - 1)
    months = ((year, month), previous)
    return [template.replace("{month}", f"{y:04d}-{m:02d}") for y, m in months]


def is_stale(path: str, now: float | None = None) -> bool:
    """True when the file is missing or older than MAX_AGE_DAYS."""
    try:
        age = (now or time.time()) - os.path.getmtime(path)
    except OSError:
        return True
    return age > MAX_AGE_DAYS * 86400


def validate(data: bytes) -> MMDBReader:
    """Parse ``data`` as an MMDB country database that answers the check
    address. Raises MMDBError otherwise."""
    if len(data) > MAX_FILE_BYTES:
        raise MMDBError("file too large")
    reader = MMDBReader(data)
    if reader.country(_CHECK_IP) != _CHECK_COUNTRY:
        raise MMDBError("check address not found in database")
    return reader


def _unpack(body: bytes) -> bytes:
    """Gunzip when the body is gzip (by its magic bytes), else as is."""
    if body[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
            data = gz.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise MMDBError("file too large")
        return data
    return body


def _download(url: str, client: httpx.Client) -> bytes:
    if not url.startswith("https://"):
        raise MMDBError("only https URLs are accepted")
    chunks: list[bytes] = []
    size = 0
    with client.stream("GET", url) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                raise MMDBError("download too large")
            chunks.append(chunk)
    return _unpack(b"".join(chunks))


def _write_atomically(path: str, data: bytes) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".ip-country-", suffix=".mmdb")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def refresh(force: bool = False, client: httpx.Client | None = None) -> str:
    """Download and install the database when it is missing or stale.
    Returns what happened: off, fresh, updated, or failed. Never raises."""
    template, path = fetch_configured()
    if not template or not path:
        return "off"
    if not force and not is_stale(path):
        return "fresh"
    own_client = client is None
    client = client or httpx.Client(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True)
    try:
        for url in candidate_urls(template):
            try:
                data = _download(url, client)
                reader = validate(data)
                _write_atomically(path, data)
                logger.info(
                    "ip country database updated from %s (%s, built %s)",
                    url,
                    reader.metadata.get("database_type"),
                    reader.metadata.get("build_epoch"),
                )
                return "updated"
            except (httpx.HTTPError, MMDBError, OSError, ValueError, EOFError) as e:
                logger.warning("ip country database download failed (%s): %s", url, e)
        return "failed"
    finally:
        if own_client:
            client.close()


async def run_forever() -> None:
    """Refresh at startup, then check once a day."""
    while True:
        try:
            await asyncio.to_thread(refresh)
        except Exception as e:  # noqa: BLE001 - a background job must not die
            logger.warning("ip country database refresh crashed: %s", type(e).__name__)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
