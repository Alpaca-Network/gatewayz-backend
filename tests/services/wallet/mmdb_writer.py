"""A minimal MaxMind DB (MMDB) writer for tests: builds small country
databases on disk so no test reads a real geo database or the network.

Follows the public MMDB format specification: a binary search tree of
24/28/32-bit records, a 16-byte separator, the data section, then the
metadata marker and metadata map.
"""

from __future__ import annotations

import ipaddress
import struct
from pathlib import Path
from typing import Any

METADATA_MARKER = b"\xab\xcd\xefMaxMind.com"


class Raw(bytes):
    """Pre-encoded bytes placed in the data section as they are."""


def pointer(offset: int) -> Raw:
    """A pointer to ``offset`` in the data section (all four sizes)."""
    if offset < 2048:
        return Raw(bytes([0x20 | (offset >> 8), offset & 0xFF]))
    if offset < 526336:
        v = offset - 2048
        return Raw(bytes([0x28 | (v >> 16)]) + (v & 0xFFFF).to_bytes(2, "big"))
    if offset < 134744064:
        v = offset - 526336
        return Raw(bytes([0x30 | (v >> 24)]) + (v & 0xFFFFFF).to_bytes(3, "big"))
    return Raw(bytes([0x38]) + offset.to_bytes(4, "big"))


class UInt:
    """An unsigned integer of an explicit MMDB type (5, 6, 9 or 10)."""

    def __init__(self, kind: int, value: int):
        self.kind, self.value = kind, value


class Int32:
    def __init__(self, value: int):
        self.value = value


class Float:
    def __init__(self, value: float):
        self.value = value


def _control(kind: int, size: int) -> bytes:
    if size < 29:
        first, extra = size, b""
    elif size < 285:
        first, extra = 29, bytes([size - 29])
    elif size < 65821:
        first, extra = 30, (size - 285).to_bytes(2, "big")
    else:
        first, extra = 31, (size - 65821).to_bytes(3, "big")
    if kind <= 7:
        return bytes([(kind << 5) | first]) + extra
    return bytes([first, kind - 7]) + extra


def encode(value: Any) -> bytes:
    if isinstance(value, Raw):
        return bytes(value)
    if isinstance(value, bool):
        return _control(14, int(value))
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return _control(2, len(raw)) + raw
    if isinstance(value, bytes):
        return _control(4, len(value)) + value
    if isinstance(value, float):
        return _control(3, 8) + struct.pack(">d", value)
    if isinstance(value, Float):
        return _control(15, 4) + struct.pack(">f", value.value)
    if isinstance(value, Int32):
        return _control(8, 4) + struct.pack(">i", value.value)
    if isinstance(value, UInt):
        raw = value.value.to_bytes((value.value.bit_length() + 7) // 8, "big")
        return _control(value.kind, len(raw)) + raw
    if isinstance(value, int):
        kind = 6 if value < 1 << 32 else 9
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return _control(kind, len(raw)) + raw
    if isinstance(value, dict):
        out = _control(7, len(value))
        for key, item in value.items():
            out += encode(key) + encode(item)
        return out
    if isinstance(value, list):
        return _control(11, len(value)) + b"".join(encode(v) for v in value)
    raise TypeError(type(value))


class _Node:
    def __init__(self):
        self.children: list[Any] = [None, None]


def _bits(network: str, ip_version: int) -> list[int]:
    net = ipaddress.ip_network(network)
    if net.version == 4 and ip_version == 6:
        value, length = int(net.network_address), 96 + net.prefixlen
        total = 128
    else:
        value, length = int(net.network_address), net.prefixlen
        total = net.max_prefixlen
    return [(value >> (total - 1 - i)) & 1 for i in range(length)]


def build_mmdb(
    networks: dict[str, Any],
    *,
    record_size: int = 24,
    ip_version: int = 6,
    database_type: str = "Test-Country",
    build_epoch: int = 1_700_000_000,
    raw_tree: bytes | None = None,
    node_count: int | None = None,
    data_prefix: bytes = b"",
) -> bytes:
    """MMDB bytes mapping each network to its record. ``data_prefix`` is put
    at the start of the data section (for hand-made pointers). ``raw_tree``
    and ``node_count`` replace the generated tree (for corrupt files)."""
    data = bytearray(data_prefix)
    offsets: dict[int, int] = {}
    root = _Node()
    for network, record in networks.items():
        encoded = encode(record)
        if id(record) not in offsets:
            offsets[id(record)] = len(data)
            data += encoded
        node = root
        bits = _bits(network, ip_version)
        for bit in bits[:-1]:
            if not isinstance(node.children[bit], _Node):
                node.children[bit] = _Node()
            node = node.children[bit]
        node.children[bits[-1]] = ("data", offsets[id(record)])

    nodes: list[_Node] = []
    index: dict[int, int] = {}
    queue = [root]
    while queue:
        node = queue.pop(0)
        index[id(node)] = len(nodes)
        nodes.append(node)
        queue += [c for c in node.children if isinstance(c, _Node)]
    count = len(nodes)

    def value(child: Any) -> int:
        if child is None:
            return count
        if isinstance(child, _Node):
            return index[id(child)]
        return count + 16 + child[1]

    tree = bytearray()
    for node in nodes:
        left, right = (value(c) for c in node.children)
        if record_size == 24:
            tree += left.to_bytes(3, "big") + right.to_bytes(3, "big")
        elif record_size == 28:
            middle = ((left >> 24) << 4) | (right >> 24)
            tree += (
                (left & 0xFFFFFF).to_bytes(3, "big")
                + bytes([middle])
                + (right & 0xFFFFFF).to_bytes(3, "big")
            )
        else:
            tree += left.to_bytes(4, "big") + right.to_bytes(4, "big")

    if raw_tree is not None:
        tree = bytearray(raw_tree)
        count = node_count if node_count is not None else count
    metadata = {
        "binary_format_major_version": UInt(5, 2),
        "binary_format_minor_version": UInt(5, 0),
        "build_epoch": UInt(9, build_epoch),
        "database_type": database_type,
        "description": {"en": "test database"},
        "ip_version": UInt(5, ip_version),
        "languages": ["en"],
        "node_count": UInt(6, count),
        "record_size": UInt(5, record_size),
    }
    return bytes(tree) + b"\x00" * 16 + bytes(data) + METADATA_MARKER + encode(metadata)


def write_mmdb(path: Path, networks: dict[str, Any], **kwargs: Any) -> Path:
    path.write_bytes(build_mmdb(networks, **kwargs))
    return path
