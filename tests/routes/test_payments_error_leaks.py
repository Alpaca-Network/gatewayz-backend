"""Payments routes must not echo Stripe or database exception text."""

import asyncio
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.routes import payments


def _run(coro):
    """Run a coroutine on a private loop.

    Not asyncio.run(): that unsets the thread's current event loop on exit,
    which breaks later tests in the same worker that call get_event_loop().
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


SECRET_TEXT = "No such price: price_1Abc; request req_stripe_xyz on acct_1Secret"


def test_credit_packages_error_is_generic():
    with patch.object(
        payments.stripe_service, "get_credit_packages", side_effect=RuntimeError(SECRET_TEXT)
    ):
        with pytest.raises(HTTPException) as exc_info:
            _run(payments.get_credit_packages())

    assert exc_info.value.status_code == 500
    assert SECRET_TEXT not in str(exc_info.value.detail)
    assert "request id" in exc_info.value.detail


def test_checkout_session_lookup_error_is_generic():
    with patch.object(
        payments.stripe_service,
        "retrieve_checkout_session",
        side_effect=RuntimeError(SECRET_TEXT),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _run(payments.get_checkout_session("cs_test_1", current_user={"id": 1}))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Checkout session not found"
