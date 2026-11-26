"""Country of an IP address from a local MaxMind DB (MMDB) file.

Answers one question -- the ISO 3166-1 alpha-2 country of an IP address --
for the Cardano wallet-link country check (src/services/wallet/eligibility.py).
No third-party package: a small reader written against the public MMDB
format specification (https://maxmind.github.io/MaxMind-DB/). It works with
the DB-IP "IP to Country Lite" and MaxMind GeoLite2 Country databases; see
docs/wallet/ip-country.md for how the file is provided.

Config.WALLET_LINK_IP_COUNTRY_DB_PATH names the file; empty (the default)
turns the lookup off. The file is read once into memory at first use and read
again when its modification time changes. Lookups only read immutable bytes,
so they are safe from any thread.

Nothing here raises to callers: an unknown, private or malformed address, a
missing or corrupt file, all give None. Every read is bounds-checked, and
pointer chains, nesting and the number of decoded values are capped, so a
corrupt file can neither loop nor index out of range.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import struct
import threading
from typing import Any

from src.config.config import Config

logger = logging.getLogger(__name__)

_METADATA_MARKER = b"\xab\xcd\xefMaxMind.com"
# The metadata section is at most 128 KiB and sits at the end of the file.
_METADATA_MAX_SIZE = 128 * 1024
_DATA_SEPARATOR_SIZE = 16
# A country database is a few MB; refuse anything absurd rather than read it.
_MAX_FILE_SIZE = 512 * 1024 * 1024
# Decoder limits: nesting of maps / arrays (pointers count as one level each),
# and decoded values per lookup.
_MAX_DEPTH = 32
_MAX_VALUES = 10_000

# Data type numbers from the specification.
_EXTENDED = 0
_POINTER = 1
_UTF8 = 2
_DOUBLE = 3
_BYTES = 4
_UINT16 = 5
_UINT32 = 6
_MAP = 7
_INT32 = 8
_UINT64 = 9
_UINT128 = 10
_ARRAY = 11
_CONTAINER = 12
_END_MARKER = 13
_BOOLEAN = 14
_FLOAT = 15

_UINT_MAX_BYTES = {_UINT16: 2, _UINT32: 4, _UINT64: 8, _UINT128: 16}


class MMDBError(ValueError):
    """The file is not a valid MMDB file, or a read went out of bounds."""


class _Decoder:
    """Decodes values from one section (data or metadata) of the buffer.

    ``base`` is where the section starts; pointers are offsets from it.
    """

    def __init__(self, buf: bytes, base: int, end: int):
        self._buf = buf
        self._base = base
        self._end = end
        self._budget = _MAX_VALUES

    def _read(self, offset: int, size: int) -> bytes:
        if size < 0 or offset < self._base or offset + size > self._end:
            raise MMDBError("read out of bounds")
        return self._buf[offset : offset + size]

    def decode(self, offset: int, depth: int = 0) -> tuple[Any, int]:
        """(value, offset just past it). ``offset`` is absolute."""
        if depth > _MAX_DEPTH:
            raise MMDBError("data nested too deeply")
        self._budget -= 1
        if self._budget < 0:
            raise MMDBError("too many values")

        ctrl = self._read(offset, 1)[0]
        offset += 1
        kind = ctrl >> 5

        if kind == _POINTER:
            target, offset = self._pointer(ctrl, offset)
            value, _ = self.decode(self._base + target, depth + 1)
            return value, offset

        if kind == _EXTENDED:
            kind = 7 + self._read(offset, 1)[0]
            offset += 1
            if kind < 8 or kind > _FLOAT:
                raise MMDBError("unknown extended type")

        size, offset = self._size(ctrl, offset)

        if kind == _MAP:
            result: dict[str, Any] = {}
            for _ in range(size):
                key, offset = self.decode(offset, depth + 1)
                if not isinstance(key, str):
                    raise MMDBError("map key is not a string")
                result[key], offset = self.decode(offset, depth + 1)
            return result, offset
        if kind == _ARRAY:
            items = []
            for _ in range(size):
                item, offset = self.decode(offset, depth + 1)
                items.append(item)
            return items, offset
        if kind == _BOOLEAN:
            if size > 1:
                raise MMDBError("bad boolean")
            return bool(size), offset
        if kind in (_CONTAINER, _END_MARKER):
            raise MMDBError("unsupported data type")

        raw = self._read(offset, size)
        offset += size
        if kind == _UTF8:
            try:
                return raw.decode("utf-8"), offset
            except UnicodeDecodeError as e:
                raise MMDBError("bad utf-8 string") from e
        if kind == _BYTES:
            return raw, offset
        if kind == _DOUBLE:
            if size != 8:
                raise MMDBError("bad double")
            return struct.unpack(">d", raw)[0], offset
        if kind == _FLOAT:
            if size != 4:
                raise MMDBError("bad float")
            return struct.unpack(">f", raw)[0], offset
        if kind == _INT32:
            if size > 4:
                raise MMDBError("bad int32")
            value = int.from_bytes(raw, "big")
            if size == 4 and value >= 1 << 31:
                value -= 1 << 32
            return value, offset
        if size > _UINT_MAX_BYTES[kind]:
            raise MMDBError("unsigned integer too large")
        return int.from_bytes(raw, "big"), offset

    def _pointer(self, ctrl: int, offset: int) -> tuple[int, int]:
        size = (ctrl >> 3) & 0x3
        low = ctrl & 0x7
        if size == 0:
            return (low << 8) | self._read(offset, 1)[0], offset + 1
        if size == 1:
            raw = self._read(offset, 2)
            return ((low << 16) | int.from_bytes(raw, "big")) + 2048, offset + 2
        if size == 2:
            raw = self._read(offset, 3)
            return ((low << 24) | int.from_bytes(raw, "big")) + 526336, offset + 3
        return int.from_bytes(self._read(offset, 4), "big"), offset + 4

    def _size(self, ctrl: int, offset: int) -> tuple[int, int]:
        size = ctrl & 0x1F
        if size < 29:
            return size, offset
        if size == 29:
            return 29 + self._read(offset, 1)[0], offset + 1
        if size == 30:
            return 285 + int.from_bytes(self._read(offset, 2), "big"), offset + 2
        return 65821 + int.from_bytes(self._read(offset, 3), "big"), offset + 3


class MMDBReader:
    """An MMDB file held in memory. Construction validates the metadata and
    raises MMDBError; :meth:`country` never raises."""

    def __init__(self, buf: bytes):
        self._buf = buf
        marker = buf.rfind(_METADATA_MARKER, max(0, len(buf) - _METADATA_MAX_SIZE))
        if marker < 0:
            raise MMDBError("metadata marker not found")
        meta_start = marker + len(_METADATA_MARKER)
        metadata, _ = _Decoder(buf, meta_start, len(buf)).decode(meta_start)
        if not isinstance(metadata, dict):
            raise MMDBError("metadata is not a map")

        node_count = metadata.get("node_count")
        record_size = metadata.get("record_size")
        ip_version = metadata.get("ip_version")
        if not isinstance(node_count, int) or node_count <= 0:
            raise MMDBError("bad node_count")
        if record_size not in (24, 28, 32):
            raise MMDBError("unsupported record_size")
        if ip_version not in (4, 6):
            raise MMDBError("bad ip_version")

        self.metadata = metadata
        self.node_count = node_count
        self.record_size = record_size
        self.ip_version = ip_version
        self._node_bytes = record_size // 4  # two records per node
        self._tree_size = node_count * self._node_bytes
        self._data_start = self._tree_size + _DATA_SEPARATOR_SIZE
        if self._data_start > marker:
            raise MMDBError("search tree larger than the file")
        self._data_end = marker
        self._ipv4_start = self._find_ipv4_start()

    def _record(self, node: int, bit: int) -> int:
        if node < 0 or node >= self.node_count:
            raise MMDBError("node out of range")
        start = node * self._node_bytes
        b = self._buf[start : start + self._node_bytes]
        if len(b) != self._node_bytes:
            raise MMDBError("node out of bounds")
        if self.record_size == 24:
            return int.from_bytes(b[3:6] if bit else b[0:3], "big")
        if self.record_size == 28:
            if bit:
                return ((b[3] & 0x0F) << 24) | int.from_bytes(b[4:7], "big")
            return ((b[3] & 0xF0) << 20) | int.from_bytes(b[0:3], "big")
        return int.from_bytes(b[4:8] if bit else b[0:4], "big")

    def _find_ipv4_start(self) -> int:
        """The node reached after 96 zero bits (::/96), where IPv4 addresses
        start in an IPv6 tree. It may already be a data record or the empty
        marker (node_count); _lookup handles both."""
        if self.ip_version == 4:
            return 0
        node = 0
        for _ in range(96):
            if node >= self.node_count:
                break
            node = self._record(node, 0)
        return node

    def _lookup(self, packed: bytes) -> Any:
        bits = len(packed) * 8
        node = self._ipv4_start if bits == 32 else 0
        # The loop is bounded by the address length (at most 128 steps).
        for i in range(bits):
            if node >= self.node_count:
                break
            bit = (packed[i >> 3] >> (7 - (i & 7))) & 1
            node = self._record(node, bit)
        if node == self.node_count:
            return None  # empty: no data for this network
        if node < self.node_count:
            raise MMDBError("search tree deeper than the address")
        offset = self._tree_size + (node - self.node_count)
        if offset < self._data_start or offset >= self._data_end:
            raise MMDBError("data pointer out of bounds")
        value, _ = _Decoder(self._buf, self._data_start, self._data_end).decode(offset)
        return value

    def country(self, ip: str) -> str | None:
        """ISO alpha-2 country for ``ip`` (``country.iso_code``, else
        ``registered_country.iso_code``), or None."""
        try:
            addr = ipaddress.ip_address(str(ip).strip())
            if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
                addr = addr.ipv4_mapped
            if not addr.is_global:
                return None
            if addr.version == 6 and self.ip_version == 4:
                return None
            record = self._lookup(addr.packed)
        except (ValueError, TypeError, IndexError, struct.error):
            # MMDBError is a ValueError: a corrupt record reads as unknown.
            return None
        if not isinstance(record, dict):
            return None
        for key in ("country", "registered_country"):
            section = record.get(key)
            code = section.get("iso_code") if isinstance(section, dict) else None
            if isinstance(code, str) and len(code) == 2 and code.isascii() and code.isalpha():
                return code.upper()
        return None


# Module state: (key, reader, error) for one (path, mtime), replaced as a whole
# so a lock-free read always sees a consistent triple.
_lock = threading.Lock()
_state: tuple[Any, MMDBReader | None, str | None] = (None, None, None)


def _configured_path() -> str:
    return (getattr(Config, "WALLET_LINK_IP_COUNTRY_DB_PATH", "") or "").strip()


def ip_country_enabled() -> bool:
    """True when a database path is configured (it may still be unreadable)."""
    return bool(_configured_path())


def _load(path: str) -> tuple[MMDBReader | None, str | None]:
    try:
        size = os.path.getsize(path)
        if size > _MAX_FILE_SIZE:
            raise MMDBError("file too large")
        with open(path, "rb") as f:
            buf = f.read()
        return MMDBReader(buf), None
    except (OSError, ValueError, TypeError, IndexError, struct.error) as e:
        return None, f"{type(e).__name__}: {e}"


def _reader() -> MMDBReader | None:
    """The reader for the configured file, (re)loaded when the path or the
    file's mtime changed. None when off, missing or unreadable."""
    path = _configured_path()
    if not path:
        return None
    global _state
    try:
        key: tuple[str, int | None] = (path, os.stat(path).st_mtime_ns)
    except OSError:
        key = (path, None)
    cached = _state
    if cached[0] == key:
        return cached[1]
    with _lock:
        if _state[0] == key:
            return _state[1]
        if key[1] is None:
            reader, error = None, "file not found"
        else:
            reader, error = _load(path)
        if error:
            # Once per (path, mtime): the next attempt is after the file changes.
            logger.warning("ip country database could not be loaded (%s): %s", path, error)
        _state = (key, reader, error)
        return reader


def country_for_ip(ip: str | None) -> str | None:
    """ISO alpha-2 country for ``ip`` from the configured database, or None
    (lookup off, file missing or unreadable, address unknown, private or
    malformed). Never raises."""
    if not ip:
        return None
    try:
        reader = _reader()
        return reader.country(ip) if reader else None
    except Exception as e:  # noqa: BLE001 - never break the caller
        logger.warning("ip country lookup failed: %s", type(e).__name__)
        return None


def ip_country_status() -> dict[str, Any]:
    """For ops pages: whether the database is configured and loaded, which
    file, its database type and build epoch, and the load error if any."""
    path = _configured_path()
    try:
        reader = _reader()
        error = _state[2]
    except Exception as e:  # noqa: BLE001
        reader, error = None, type(e).__name__
    meta = reader.metadata if reader else {}
    return {
        "configured": bool(path),
        "loaded": reader is not None,
        "path": path or None,
        "database_type": meta.get("database_type"),
        "build_epoch": meta.get("build_epoch"),
        "error": error if path and reader is None else None,
    }
