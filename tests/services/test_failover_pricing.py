"""Unit tests for the failover loss-proof cost split.

Covers src.handlers.chat_handler._loss_proof_cost_split, which ensures a request
served (after provider selection / failover) by a different, pricier provider is
never billed below that served provider's actual cost. Pure unit tests — all
pricing lookups are mocked, no DB.
"""

from unittest.mock import patch

from src.handlers.chat_handler import _loss_proof_cost_split


def _split(total):
    """Helper: a (total, input, output) split with a simple 60/40 attribution."""
    return (total, round(total * 0.6, 6), round(total * 0.4, 6))


def test_no_provider_model_id_returns_base():
    with patch(
        "src.handlers.chat_handler.calculate_cost_split", return_value=_split(1.0)
    ) as base:
        assert _loss_proof_cost_split("openai/gpt-4o", None, 100, 50) == _split(1.0)
        base.assert_called_once()


def test_served_equals_requested_returns_base():
    with patch(
        "src.handlers.chat_handler.calculate_cost_split", return_value=_split(1.0)
    ):
        # provider_model_id identical to requested → no second lookup, base cost
        assert _loss_proof_cost_split("openai/gpt-4o", "openai/gpt-4o", 10, 10) == _split(1.0)


def test_pricier_served_provider_is_billed():
    # Requested model resolves to $0.20; served provider actually costs $0.50.
    def fake_split(model_id, p, c):
        return _split(0.20) if model_id == "req/model" else _split(0.50)

    with patch("src.handlers.chat_handler.calculate_cost_split", side_effect=fake_split), patch(
        "src.handlers.chat_handler.get_model_pricing",
        return_value={"prompt": 0.000001, "completion": 0.000002, "source": "database"},
    ):
        total, _, _ = _loss_proof_cost_split("req/model", "served/model", 100, 50)
        assert total == 0.50  # billed the higher served cost — no loss


def test_cheaper_served_provider_keeps_advertised_rate():
    # Served provider is cheaper than the advertised model price → keep advertised
    # (never bill the customer below the rate they requested).
    def fake_split(model_id, p, c):
        return _split(0.50) if model_id == "req/model" else _split(0.20)

    with patch("src.handlers.chat_handler.calculate_cost_split", side_effect=fake_split), patch(
        "src.handlers.chat_handler.get_model_pricing",
        return_value={"prompt": 0.000001, "completion": 0.000002, "source": "database"},
    ):
        total, _, _ = _loss_proof_cost_split("req/model", "served/model", 100, 50)
        assert total == 0.50  # advertised rate retained


def test_default_source_served_pricing_ignored():
    # If the served provider only has DEFAULT pricing, do NOT use it (could be wrong).
    def fake_split(model_id, p, c):
        return _split(0.20) if model_id == "req/model" else _split(9.99)

    with patch("src.handlers.chat_handler.calculate_cost_split", side_effect=fake_split), patch(
        "src.handlers.chat_handler.get_model_pricing",
        return_value={"prompt": 0.00002, "completion": 0.00002, "source": "default"},
    ):
        total, _, _ = _loss_proof_cost_split("req/model", "served/model", 100, 50)
        assert total == 0.20  # default-sourced served price ignored


def test_zero_served_pricing_ignored():
    def fake_split(model_id, p, c):
        return _split(0.20) if model_id == "req/model" else _split(0.0)

    with patch("src.handlers.chat_handler.calculate_cost_split", side_effect=fake_split), patch(
        "src.handlers.chat_handler.get_model_pricing",
        return_value={"prompt": 0.0, "completion": 0.0, "source": "database"},
    ):
        total, _, _ = _loss_proof_cost_split("req/model", "served/model", 100, 50)
        assert total == 0.20  # zero served price ignored


def test_served_lookup_exception_falls_back_to_base():
    def fake_split(model_id, p, c):
        if model_id == "served/model":
            raise ValueError("high-value model missing pricing")
        return _split(0.20)

    with patch("src.handlers.chat_handler.calculate_cost_split", side_effect=fake_split), patch(
        "src.handlers.chat_handler.get_model_pricing",
        return_value={"prompt": 0.000001, "completion": 0.000002, "source": "database"},
    ):
        total, _, _ = _loss_proof_cost_split("req/model", "served/model", 100, 50)
        assert total == 0.20  # safe fallback to requested-model cost
