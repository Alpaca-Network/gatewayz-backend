"""Tests for src/services/wallet/ip_country.py, the MMDB country reader.

Every database here is written by tests/services/wallet/mmdb_writer.py into
tmp_path: no real geo database, no network.
"""

from __future__ import annotations

import logging
import os

import pytest

from src.services.wallet import ip_country
from src.services.wallet.ip_country import MMDBError, MMDBReader
from tests.services.wallet.mmdb_writer import (
    Float,
    Int32,
    Raw,
    UInt,
    build_mmdb,
    pointer,
    write_mmdb,
)

US = {"country": {"iso_code": "US", "names": {"en": "United States"}}}
CA = {"registered_country": {"iso_code": "CA"}}
PT = {"country": {"iso_code": "PT"}, "registered_country": {"iso_code": "ES"}}

V4 = {"8.8.8.0/24": US, "24.48.0.0/16": CA, "85.240.0.0/13": PT}
V6 = {"2001:4860::/32": US, "2607:f2c0::/32": CA, "2001:8a0::/32": PT}


def _reader(networks, **kwargs) -> MMDBReader:
    return MMDBReader(build_mmdb(networks, **kwargs))


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Point the module at a fresh file and forget any cached state."""
    path = tmp_path / "country.mmdb"
    monkeypatch.setattr(ip_country.Config, "WALLET_LINK_IP_COUNTRY_DB_PATH", str(path))
    monkeypatch.setattr(ip_country, "_state", (None, None, None))
    return path


@pytest.mark.parametrize("record_size", [24, 28, 32])
class TestLookups:
    def test_ipv4_in_an_ipv6_tree(self, record_size):
        reader = _reader({**V4, **V6}, record_size=record_size)
        assert reader.country("8.8.8.8") == "US"
        assert reader.country("24.48.12.1") == "CA"  # registered_country
        assert reader.country("85.243.1.2") == "PT"  # country wins over registered
        assert reader.country("::ffff:8.8.8.8") == "US"

    def test_ipv6(self, record_size):
        reader = _reader({**V4, **V6}, record_size=record_size)
        assert reader.country("2001:4860:4860::8888") == "US"
        assert reader.country("2607:f2c0:1::1") == "CA"
        assert reader.country("2001:8a0:abcd::1") == "PT"

    def test_ipv4_only_tree(self, record_size):
        reader = _reader(V4, record_size=record_size, ip_version=4)
        assert reader.country("8.8.8.8") == "US"
        assert reader.country("85.240.0.1") == "PT"
        assert reader.country("2001:4860:4860::8888") is None

    def test_unknown_private_and_malformed(self, record_size):
        reader = _reader({**V4, **V6}, record_size=record_size)
        for ip in ("1.1.1.1", "2400:cb00::1", "10.0.0.1", "127.0.0.1", "::1", "fe80::1"):
            assert reader.country(ip) is None, ip
        for ip in ("", "not-an-ip", "999.1.1.1", "8.8.8", "8.8.8.8/24", None, 12):
            assert reader.country(ip) is None, ip


def test_large_record_size_28_values():
    # Records above 2**24 use the shared middle nibble of a 28-bit node.
    filler = "x" * (1 << 24)
    reader = _reader({"8.8.8.0/24": US}, record_size=28, data_prefix=filler.encode())
    assert reader.country("8.8.8.8") == "US"


def test_every_data_type_decodes():
    record = {
        "country": {"iso_code": "PT"},
        "long": "y" * 300,
        "longer": "z" * 70000,
        "medium": "w" * 40,
        "bytes": b"\x00\x01",
        "double": 1.5,
        "float": Float(0.5),
        "int32": Int32(-7),
        "u16": UInt(5, 65535),
        "u32": UInt(6, 1 << 31),
        "u64": UInt(9, 1 << 63),
        "u128": UInt(10, 1 << 127),
        "bool": True,
        "array": [1, "a", False],
        "empty_map": {},
    }
    reader = _reader({"85.240.0.0/13": record})
    value = reader._lookup(bytes([85, 240, 0, 1]))
    assert value["long"] == "y" * 300 and len(value["longer"]) == 70000
    assert value["medium"] == "w" * 40
    assert value["bytes"] == b"\x00\x01"
    assert (value["double"], value["float"], value["int32"]) == (1.5, 0.5, -7)
    assert (value["u16"], value["u32"], value["u64"]) == (65535, 1 << 31, 1 << 63)
    assert value["u128"] == 1 << 127
    assert value["bool"] is True and value["array"] == [1, "a", False]
    assert reader.country("85.240.0.1") == "PT"


def test_pointers_are_followed():
    # data section: a shared country map first, then records pointing at it.
    shared = bytes([0xE1, 0x48]) + b"iso_code" + bytes([0x42]) + b"PT"  # {"iso_code": "PT"}
    record = {"country": Raw(pointer(0))}
    reader = _reader({"85.240.0.0/13": record}, data_prefix=shared)
    assert reader.country("85.240.0.1") == "PT"
    # All four pointer sizes encode / decode.
    for offset in (5, 3000, 600000, 140000000):
        raw = pointer(offset)
        decoder = ip_country._Decoder(bytes(raw), 0, len(raw))
        assert decoder._pointer(raw[0], 1)[0] == offset


@pytest.mark.timeout(10)
class TestCorruptFiles:
    def test_pointer_loop_is_none_not_a_hang(self):
        # The record at data offset 0 is {"country": <pointer to offset 0>}.
        looped = bytes([0xE1, 0x47]) + b"country" + bytes(pointer(0))
        reader = _reader({"8.8.8.0/24": Raw(looped)})
        assert reader.country("8.8.8.8") is None

    def test_pointer_out_of_bounds(self):
        reader = _reader({"8.8.8.0/24": {"country": Raw(pointer(500000))}})
        assert reader.country("8.8.8.8") is None

    def test_self_referencing_tree(self):
        # One node whose both records point back to itself.
        reader = _reader(V4, raw_tree=b"\x00" * 6, node_count=1)
        assert reader.country("8.8.8.8") is None
        assert reader.country("2001:4860::1") is None

    def test_tree_record_beyond_the_data_section(self):
        tree = (1).to_bytes(3, "big") + (0xFFFFFF).to_bytes(3, "big")
        reader = _reader(V4, raw_tree=tree, node_count=1, ip_version=4)
        assert reader.country("200.1.1.1") is None

    def test_truncated_files_do_not_load(self):
        data = build_mmdb(V4)
        for cut in (0, 10, len(data) // 2, len(data) - 5):
            with pytest.raises(MMDBError):
                MMDBReader(data[:cut])

    def test_bad_metadata(self):
        with pytest.raises(MMDBError):
            MMDBReader(build_mmdb(V4, record_size=20))
        with pytest.raises(MMDBError):
            MMDBReader(b"\x00" * 16 + ip_country._METADATA_MARKER + b"\x42PT")
        # A tree larger than the file.
        with pytest.raises(MMDBError):
            MMDBReader(build_mmdb(V4, raw_tree=b"", node_count=1_000_000))

    def test_truncated_data_section_is_none(self):
        data = build_mmdb({"8.8.8.0/24": {"country": {"iso_code": "US"}}})
        marker = data.rfind(ip_country._METADATA_MARKER)
        # Drop the last bytes of the record, keep the metadata.
        reader = MMDBReader(data[: marker - 3] + data[marker:])
        assert reader.country("8.8.8.8") is None


class TestModuleLoading:
    def test_off_when_unconfigured(self, db_path, monkeypatch):
        monkeypatch.setattr(ip_country.Config, "WALLET_LINK_IP_COUNTRY_DB_PATH", "")
        assert not ip_country.ip_country_enabled()
        assert ip_country.country_for_ip("8.8.8.8") is None
        assert ip_country.ip_country_status() == {
            "configured": False,
            "loaded": False,
            "path": None,
            "database_type": None,
            "build_epoch": None,
            "error": None,
        }

    def test_lookup_and_status(self, db_path):
        write_mmdb(db_path, {**V4, **V6}, database_type="DBIP-Country-Lite", build_epoch=123)
        assert ip_country.country_for_ip("8.8.8.8") == "US"
        assert ip_country.country_for_ip("2001:8a0::1") == "PT"
        assert ip_country.country_for_ip(None) is None
        status = ip_country.ip_country_status()
        assert status["loaded"] and status["error"] is None
        assert (status["database_type"], status["build_epoch"]) == ("DBIP-Country-Lite", 123)
        assert status["path"] == str(db_path)

    def test_reload_when_mtime_changes(self, db_path):
        write_mmdb(db_path, {"8.8.8.0/24": US})
        assert ip_country.country_for_ip("8.8.8.8") == "US"
        write_mmdb(db_path, {"8.8.8.0/24": PT})
        stat = os.stat(db_path)
        os.utime(db_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        assert ip_country.country_for_ip("8.8.8.8") == "PT"

    def test_missing_file_logs_once(self, db_path, caplog):
        with caplog.at_level(logging.WARNING, logger=ip_country.logger.name):
            for _ in range(3):
                assert ip_country.country_for_ip("8.8.8.8") is None
        assert len(caplog.records) == 1
        status = ip_country.ip_country_status()
        assert status["configured"] and not status["loaded"]
        assert status["error"] == "file not found"

    @pytest.mark.timeout(10)
    def test_corrupt_file_is_none_and_recovers(self, db_path, caplog):
        db_path.write_bytes(b"not an mmdb file")
        with caplog.at_level(logging.WARNING, logger=ip_country.logger.name):
            assert ip_country.country_for_ip("8.8.8.8") is None
            assert ip_country.country_for_ip("8.8.8.8") is None
        assert len(caplog.records) == 1
        assert "MMDBError" in ip_country.ip_country_status()["error"]
        write_mmdb(db_path, {"8.8.8.0/24": US})
        stat = os.stat(db_path)
        os.utime(db_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        assert ip_country.country_for_ip("8.8.8.8") == "US"
