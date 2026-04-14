"""
Prometheus remote write protobuf definitions.

This module contains the protobuf message definitions required for the Prometheus
remote write protocol. These are defined using google.protobuf.message to match
the official Prometheus protobuf specifications from:
https://github.com/prometheus/prometheus/blob/main/prompb/types.proto
https://github.com/prometheus/prometheus/blob/main/prompb/remote.proto

The wire format matches exactly what Prometheus expects for remote_write.
"""


class Label:
    """
    Prometheus Label message.

    message Label {
        string name  = 1;
        string value = 2;
    }
    """

    def __init__(self, name: str = "", value: str = ""):
        self.name = name
        self.value = value

    def SerializeToString(self) -> bytes:
        """Serialize to protobuf wire format."""
        result = b""
        # Field 1: name (string)
        if self.name:
            result += b"\x0a"  # field 1, wire type 2 (length-delimited)
            name_bytes = self.name.encode("utf-8")
            result += _encode_varint(len(name_bytes))
            result += name_bytes
        # Field 2: value (string)
        if self.value:
            result += b"\x12"  # field 2, wire type 2 (length-delimited)
            value_bytes = self.value.encode("utf-8")
            result += _encode_varint(len(value_bytes))
            result += value_bytes
        return result


class Sample:
    """
    Prometheus Sample message.

    message Sample {
        double value    = 1;
        int64 timestamp = 2;
    }
    """

    def __init__(self, value: float = 0.0, timestamp: int = 0):
        self.value = value
        self.timestamp = timestamp

    def SerializeToString(self) -> bytes:
        """Serialize to protobuf wire format."""
        import struct

        result = b""
        # Field 1: value (double)
        if self.value != 0.0:
            result += b"\x09"  # field 1, wire type 1 (64-bit)
            result += struct.pack("<d", self.value)
        # Field 2: timestamp (int64)
        if self.timestamp != 0:
            result += b"\x10"  # field 2, wire type 0 (varint)
            result += _encode_varint(self.timestamp)
        return result


class TimeSeries:
    """
    Prometheus TimeSeries message.

    message TimeSeries {
        repeated Label labels   = 1;
        repeated Sample samples = 2;
    }
    """

    def __init__(self):
        self.labels: list[Label] = []
        self.samples: list[Sample] = []

    def SerializeToString(self) -> bytes:
        """Serialize to protobuf wire format."""
        result = b""
        # Field 1: labels (repeated Label)
        for label in self.labels:
            label_bytes = label.SerializeToString()
            result += b"\x0a"  # field 1, wire type 2 (length-delimited)
            result += _encode_varint(len(label_bytes))
            result += label_bytes
        # Field 2: samples (repeated Sample)
        for sample in self.samples:
            sample_bytes = sample.SerializeToString()
            result += b"\x12"  # field 2, wire type 2 (length-delimited)
            result += _encode_varint(len(sample_bytes))
            result += sample_bytes
        return result


class WriteRequest:
    """
    Prometheus WriteRequest message for remote_write.

    message WriteRequest {
        repeated TimeSeries timeseries = 1;
    }
    """

    def __init__(self):
        self.timeseries: list[TimeSeries] = []

    def SerializeToString(self) -> bytes:
        """Serialize to protobuf wire format."""
        result = b""
        # Field 1: timeseries (repeated TimeSeries)
        for ts in self.timeseries:
            ts_bytes = ts.SerializeToString()
            result += b"\x0a"  # field 1, wire type 2 (length-delimited)
            result += _encode_varint(len(ts_bytes))
            result += ts_bytes
        return result


def _encode_varint(value: int) -> bytes:
    """
    Encode an integer as a protobuf varint.

    For signed integers (int64), protobuf uses two's complement representation
    which requires handling negative values by treating them as unsigned 64-bit.

    Args:
        value: Integer to encode (must be non-negative for timestamps/lengths)

    Returns:
        Varint-encoded bytes

    Raises:
        ValueError: If value is negative (not supported for our use case)
    """
    if value < 0:
        raise ValueError(
            f"Negative values not supported for varint encoding: {value}. "
            "Prometheus timestamps and lengths must be non-negative."
        )

    # Handle zero explicitly
    if value == 0:
        return b"\x00"

    result = b""
    while value:
        bits = value & 0x7F
        value >>= 7
        if value:
            result += bytes([0x80 | bits])
        else:
            result += bytes([bits])
    return result
