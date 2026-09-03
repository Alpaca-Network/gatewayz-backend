"""Tests for src.services.gpu.node_probe (Milestone 4 W-A1,
gatewayz-backend#2262 -- review fix round 1: SSRF hardening). No real
network -- `httpx.stream` and the SSRF guard are both mocked."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.services.gpu.node_probe import NodeProbeError, probe_node_models
from src.utils.ssrf_guard import SSRFBlockedError


def _fake_stream(status_code=200, chunks=(b'{"data": []}',)):
    response = MagicMock()
    response.status_code = status_code
    response.iter_bytes.return_value = iter(chunks)
    cm = MagicMock()
    cm.__enter__.return_value = response
    cm.__exit__.return_value = False
    return cm


@patch("src.services.gpu.node_probe.httpx.stream")
@patch("src.services.gpu.node_probe.assert_public_https_url")
def test_probe_blocked_by_ssrf_guard_never_makes_a_request(mock_guard, mock_stream):
    mock_guard.side_effect = SSRFBlockedError("private_address_blocked")

    with pytest.raises(NodeProbeError) as exc_info:
        probe_node_models("https://internal.example.com", "key")

    assert exc_info.value.reason == "endpoint_unreachable"
    mock_stream.assert_not_called()


@patch("src.services.gpu.node_probe.httpx.stream")
@patch("src.services.gpu.node_probe.assert_public_https_url")
def test_probe_success_returns_model_ids(mock_guard, mock_stream):
    mock_guard.return_value = "93.184.216.34"
    body = json.dumps({"data": [{"id": "llama-3.1-8b-instruct"}, {"id": "other-model"}]})
    mock_stream.return_value = _fake_stream(chunks=[body.encode()])

    result = probe_node_models("https://node.example.com", "node-key")

    assert result == {"llama-3.1-8b-instruct", "other-model"}


@patch("src.services.gpu.node_probe.httpx.stream")
@patch("src.services.gpu.node_probe.assert_public_https_url")
def test_probe_connects_to_pinned_ip_with_sni_and_host_for_original_hostname(
    mock_guard, mock_stream
):
    mock_guard.return_value = "93.184.216.34"
    mock_stream.return_value = _fake_stream()

    probe_node_models("https://node.example.com:8443/base", "node-key")

    args, kwargs = mock_stream.call_args
    assert args[0] == "GET"
    assert args[1] == "https://93.184.216.34:8443/base/v1/models"
    assert kwargs["headers"]["Host"] == "node.example.com"
    assert kwargs["headers"]["Authorization"] == "Bearer node-key"
    assert kwargs["follow_redirects"] is False
    assert kwargs["extensions"] == {"sni_hostname": "node.example.com"}
    assert kwargs["timeout"] == 5.0


@patch("src.services.gpu.node_probe.httpx.stream")
@patch("src.services.gpu.node_probe.assert_public_https_url")
def test_probe_treats_redirect_as_failure(mock_guard, mock_stream):
    mock_guard.return_value = "93.184.216.34"
    mock_stream.return_value = _fake_stream(status_code=302)

    with pytest.raises(NodeProbeError) as exc_info:
        probe_node_models("https://node.example.com", "key")

    assert exc_info.value.reason == "endpoint_unreachable"


@patch("src.services.gpu.node_probe.httpx.stream")
@patch("src.services.gpu.node_probe.assert_public_https_url")
def test_probe_rejects_non_200_status(mock_guard, mock_stream):
    mock_guard.return_value = "93.184.216.34"
    mock_stream.return_value = _fake_stream(status_code=500)

    with pytest.raises(NodeProbeError) as exc_info:
        probe_node_models("https://node.example.com", "key")

    assert exc_info.value.reason == "endpoint_unreachable"


@patch("src.services.gpu.node_probe.httpx.stream")
@patch("src.services.gpu.node_probe.assert_public_https_url")
def test_probe_enforces_response_size_cap(mock_guard, mock_stream):
    mock_guard.return_value = "93.184.216.34"
    oversized_chunk = b"a" * (1_000_000 + 1)
    mock_stream.return_value = _fake_stream(chunks=[oversized_chunk])

    with pytest.raises(NodeProbeError) as exc_info:
        probe_node_models("https://node.example.com", "key")

    assert exc_info.value.reason == "endpoint_unreachable"


@patch("src.services.gpu.node_probe.httpx.stream")
@patch("src.services.gpu.node_probe.assert_public_https_url")
def test_probe_wraps_httpx_errors(mock_guard, mock_stream):
    mock_guard.return_value = "93.184.216.34"
    mock_stream.side_effect = httpx.ConnectTimeout("timed out")

    with pytest.raises(NodeProbeError) as exc_info:
        probe_node_models("https://node.example.com", "key")

    assert exc_info.value.reason == "endpoint_unreachable"


@patch("src.services.gpu.node_probe.httpx.stream")
@patch("src.services.gpu.node_probe.assert_public_https_url")
def test_probe_rejects_unparseable_response(mock_guard, mock_stream):
    mock_guard.return_value = "93.184.216.34"
    mock_stream.return_value = _fake_stream(chunks=[b"not json"])

    with pytest.raises(NodeProbeError) as exc_info:
        probe_node_models("https://node.example.com", "key")

    assert exc_info.value.reason == "endpoint_unreachable"


@patch("src.services.gpu.node_probe.httpx.stream")
@patch("src.services.gpu.node_probe.assert_public_https_url")
def test_probe_omits_authorization_header_when_no_key_given(mock_guard, mock_stream):
    mock_guard.return_value = "93.184.216.34"
    mock_stream.return_value = _fake_stream()

    probe_node_models("https://node.example.com", "")

    _, kwargs = mock_stream.call_args
    assert "Authorization" not in kwargs["headers"]
