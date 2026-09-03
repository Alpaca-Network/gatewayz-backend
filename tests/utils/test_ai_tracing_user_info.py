"""AISpanContext.set_user_info must not attach identity to spans (threat model
L8): no exporter is configured today, but the attribute must not exist so a
future exporter can't silently ship user_id/api_key_hash alongside traces."""

from unittest.mock import MagicMock, patch

from src.utils.ai_tracing import AISpanContext


@patch("src.utils.ai_tracing.OTEL_AVAILABLE", True)
def test_user_id_is_never_set_as_span_attribute():
    span = MagicMock()
    ctx = AISpanContext(span=span)

    ctx.set_user_info(user_id="424242", tier="paid")

    attribute_calls = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
    assert "user.id" not in attribute_calls
    assert "customer.id" not in attribute_calls
    assert attribute_calls.get("user.tier") == "paid"


@patch("src.utils.ai_tracing.OTEL_AVAILABLE", True)
def test_api_key_hash_is_never_set_as_span_attribute():
    span = MagicMock()
    ctx = AISpanContext(span=span)

    ctx.set_user_info(api_key_hash="abcd1234abcd1234", tier="trial")

    attribute_calls = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
    assert "user.api_key_hash" not in attribute_calls
    assert attribute_calls.get("user.tier") == "trial"


@patch("src.utils.ai_tracing.OTEL_AVAILABLE", True)
def test_tier_only_call_still_sets_tier():
    span = MagicMock()
    ctx = AISpanContext(span=span)

    ctx.set_user_info(tier="admin")

    span.set_attribute.assert_called_once_with("user.tier", "admin")
