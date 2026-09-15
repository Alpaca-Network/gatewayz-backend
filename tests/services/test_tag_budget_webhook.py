"""Budget webhooks: fenced, advisory, and never fatal.

The dangerous part of this feature is that it takes a URL from a customer and
has the gateway fetch it. Most of these tests are about that.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.services.tag_budget import notify_budget_crossed
from src.services.webhook_target import InvalidWebhookTarget, validate_webhook_url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",  # plaintext would put spend on the wire
        "https://127.0.0.1/hook",
        "https://localhost/hook",
        "https://169.254.169.254/latest/meta-data",  # cloud metadata
        "https://example.com:8443/hook",  # non-default port
        "https://user:pw@example.com/hook",  # credentials in the url
        "",
        "https://example.com/" + "a" * 4000,
    ],
)
def test_unsafe_targets_are_refused(url):
    with pytest.raises(InvalidWebhookTarget):
        validate_webhook_url(url)


def test_delivery_revalidates_the_url_at_send_time():
    # DNS can change between registration and send -- that is what a rebinding
    # attack is. A URL that was public then and internal now must be refused.
    with patch(
        "src.services.tag_budget.validate_webhook_url",
        side_effect=InvalidWebhookTarget("resolves to 169.254.169.254"),
    ):
        with patch("src.services.tag_budget._post_json") as post:
            ok = notify_budget_crossed(
                webhook_url="https://was-public.example/hook",
                user_id=1,
                tag="init/abc",
                limit_usd=10,
                spent_usd=11,
            )
    assert ok is False
    assert not post.called, "a refused target must never be contacted"


def test_a_dead_endpoint_is_not_fatal():
    # Billing telemetry must not take down inference.
    with patch("src.services.tag_budget.validate_webhook_url", return_value=None):
        with patch("src.services.tag_budget._post_json", side_effect=OSError("connection refused")):
            assert (
                notify_budget_crossed(
                    webhook_url="https://example.com/hook",
                    user_id=1,
                    tag="init/abc",
                    limit_usd=10,
                    spent_usd=11,
                )
                is False
            )


def test_the_payload_says_it_is_advisory():
    sent = {}
    with patch("src.services.tag_budget.validate_webhook_url", return_value=None):
        with patch("src.services.tag_budget._post_json", side_effect=lambda u, p: sent.update(p)):
            notify_budget_crossed(
                webhook_url="https://example.com/hook",
                user_id=1,
                tag="init/abc",
                limit_usd=10,
                spent_usd=12.5,
            )
    assert sent["event"] == "usage.budget.crossed"
    assert sent["enforcement"] == "advisory", "a consumer must not read this as a stop signal"
    assert sent["spent_usd"] == 12.5
    assert sent["tag"] == "init/abc"


def test_the_payload_carries_no_key_or_user_identifier():
    # The notice goes to a customer-controlled endpoint. It says what was spent
    # against which tag -- never a credential, never an internal id.
    sent = {}
    with patch("src.services.tag_budget.validate_webhook_url", return_value=None):
        with patch("src.services.tag_budget._post_json", side_effect=lambda u, p: sent.update(p)):
            notify_budget_crossed(
                webhook_url="https://example.com/hook",
                user_id=4242,
                tag="init/abc",
                limit_usd=10,
                spent_usd=11,
            )
    blob = str(sent)
    assert "4242" not in blob
    assert "api_key" not in blob and "key_id" not in blob
