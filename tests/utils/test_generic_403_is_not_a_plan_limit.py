"""A 403 about WHO you are must not be reported as a billing limit.

Found 2026-09-30 by calling an admin endpoint with a non-admin key:

    403  {"type":"plan_limit_reached",
          "message":"Plan limit exceeded: Administrator privileges required",
          "detail":"You have reached your plan's usage limit. Please upgrade
                    your plan or wait for the limit to reset."}

The route raised "Administrator privileges required". The handler's generic
403 arm turned it into a plan limit, so the advice became "upgrade your plan
or wait".

No plan grants admin and no waiting confers it. This is the terminal-condition
family again, with a sharper edge than usual: the wrong advice is not merely
unhelpful, it is **expensive** -- it sends somebody to buy an upgrade to fix a
permissions problem.

ErrorCode.INSUFFICIENT_PERMISSIONS already existed, with the right status, the
right category and an honest message. Nothing had to be invented; it was
simply never wired to the generic case.

Also fixed here: the IP arm tested `"ip" in detail_lower` -- a bare substring
scan. Any forbidden message containing those two letters anywhere was reported
as an IP restriction. Same class as the streaming rate-limit scan that read a
credential's digits as a status code.
"""

from __future__ import annotations

from fastapi import HTTPException

from src.utils.error_handlers import _map_http_exception_to_detailed_error


def _map(detail: str, status: int = 403):
    return _map_http_exception_to_detailed_error(
        HTTPException(status_code=status, detail=detail)
    ).error


def test_admin_required_is_not_a_plan_limit():
    body = _map("Administrator privileges required")
    assert body.code == "INSUFFICIENT_PERMISSIONS", body.code
    assert "plan" not in (body.detail or "").lower()


def test_the_caller_is_not_told_to_pay_to_fix_a_permission():
    text = " ".join(
        filter(
            None,
            [
                _map("Administrator privileges required").message,
                _map("Administrator privileges required").detail,
                " ".join(_map("Administrator privileges required").suggestions or []),
            ],
        )
    ).lower()
    for phrase in ("upgrade your plan", "usage limit", "wait for the limit"):
        assert phrase not in text, phrase


def test_the_stated_reason_survives():
    assert "Administrator privileges required" in _map("Administrator privileges required").message


def test_a_real_plan_limit_is_still_a_plan_limit():
    # The carve-out. This must not swallow genuine billing conditions.
    assert _map("Plan limit exceeded for this tier").code == "PLAN_LIMIT_REACHED"


def test_a_real_trial_expiry_is_untouched():
    assert _map("Your trial has expired").code == "TRIAL_EXPIRED"


def test_a_real_ip_restriction_is_still_detected():
    assert _map("Request IP 203.0.113.9 is not in the allowlist").code == "IP_RESTRICTED"


def test_a_word_merely_containing_ip_is_not_an_ip_restriction():
    # "privileges" has no "ip", but plenty of forbidden messages do:
    # "recipient", "description", "multiple", "membership".
    for detail in (
        "Recipient is not permitted on this workspace",
        "Multiple descriptions are not allowed here",
    ):
        assert _map(detail).code == "INSUFFICIENT_PERMISSIONS", detail
