"""Provider budget exhaustion is terminal, not transient.

On 2026-09-21 all eleven Anthropic models returned 503 with Retry-After: 30
and the text "temporarily unavailable ... try again shortly", for over a day.
The account was funded; the condition was a budget/limit signal our gateway
classifies precisely and then reports as transient capacity.

A provider account or key that is out of budget stays out of budget until a
human tops it up or raises the limit. Retrying cannot clear it. So the status
must not invite retries, and the operator must be told to act rather than wait.

The tell that this was a bug rather than a judgement call: map_provider_error
in provider_failover already maps the identical condition to 402. Two code
paths disagreed about one cause, and /v1/messages reached the wrong one.

Same family as #2292 (exhausted request cap moved 429 -> 402) and #2297
(unknown model id moved 503 -> 400). The question is always the same: could
retrying ever help?
"""

from __future__ import annotations

from src.services.provider_failover import map_provider_error
from src.utils.errors import (
    PROVIDER_BUDGET_REASONS,
    classify_provider_budget_error,
    is_provider_budget_error,
)

BUDGET_ERRORS = [
    "Error code: 400 - your credit balance is too low to access the Anthropic API",
    "insufficient_quota: You exceeded your current quota",
    "Key exceeded its weekly limit; adjust the key in your dashboard",
    "Error code: 402 - payment required",
]


def test_every_budget_shape_is_recognised():
    for raw in BUDGET_ERRORS:
        assert is_provider_budget_error(raw), raw
        assert classify_provider_budget_error(raw) in PROVIDER_BUDGET_REASONS


def test_the_failover_mapper_already_treats_budget_as_terminal():
    # The path that was already right, pinned so the two cannot drift apart
    # again in the other direction.
    for raw in BUDGET_ERRORS:
        exc = map_provider_error("anthropic", "claude-haiku-4-5", RuntimeError(raw))
        assert exc.status_code == 402, f"{raw} -> {exc.status_code}"


def test_a_genuine_capacity_error_is_not_classified_as_budget():
    # The carve-out that keeps 503 meaningful: real upstream overload should
    # still be retryable, or the fix trades one wrong status for another.
    raw = "Error code: 529 - overloaded_error: the model is overloaded"
    assert not is_provider_budget_error(raw)
    assert map_provider_error("anthropic", "m", RuntimeError(raw)).status_code != 402


def test_an_unrelated_error_is_untouched():
    raw = "Error code: 500 - internal server error"
    assert not is_provider_budget_error(raw)


def test_a_credential_id_is_not_read_as_a_status():
    # parse_upstream_status refuses bare digit scans precisely so a hex key id
    # containing "402" cannot be read as payment-required.
    raw = "auth failed for key 9f402ab3c7de4021bb5540291c8e77aa9f402ab3c7de4021bb5540291c8e77aa"
    assert classify_provider_budget_error(raw) is None
