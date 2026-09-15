"""Tests for src.services.holdings.prices (holdings rewards USD price layer).

Every HTTP call is mocked -- these tests must never reach CoinGecko.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.services.holdings import prices as prices_module
from src.services.holdings.prices import PricePoint, get_usd_prices


@pytest.fixture
def sb():
    """No-op fixture whose mere presence bypasses the autouse DB-skip in
    tests/conftest.py -- this is a pure unit test with everything mocked."""
    return None


@pytest.fixture(autouse=True)
def _no_redis_and_clean_local_cache():
    """Force the in-process cache path and empty it between tests so one
    test's cached price can't satisfy the next test's lookup."""
    prices_module._local_cache_clear()
    with patch.object(prices_module, "_redis_client", return_value=None):
        yield
    prices_module._local_cache_clear()


def _ts(seconds_ago: int) -> int:
    return int((datetime.now(UTC) - timedelta(seconds=seconds_ago)).timestamp())


def _http_response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_fresh_price_is_returned(sb):
    payload = {"ethereum": {"usd": 2500.25, "last_updated_at": _ts(30)}}
    with patch.object(prices_module.requests, "get", return_value=_http_response(payload)) as get:
        result = get_usd_prices(["ethereum"])

    assert set(result) == {"ethereum"}
    point = result["ethereum"]
    assert isinstance(point, PricePoint)
    assert point.price == Decimal("2500.25")
    assert point.as_of.tzinfo is not None
    # Price parsed through str() so a float never loses cents to binary repr.
    assert isinstance(point.price, Decimal)
    assert get.call_count == 1


def test_request_asks_coingecko_for_last_updated_at(sb):
    payload = {"ethereum": {"usd": 1.0, "last_updated_at": _ts(1)}}
    with patch.object(prices_module.requests, "get", return_value=_http_response(payload)) as get:
        get_usd_prices(["ethereum"])

    _, kwargs = get.call_args
    assert kwargs["params"]["include_last_updated_at"] == "true"
    assert kwargs["params"]["vs_currencies"] == "usd"
    assert kwargs["params"]["ids"] == "ethereum"
    assert kwargs["timeout"] > 0


def test_stale_price_is_omitted(sb):
    """Fail closed: an old price must never be used to value a token."""
    stale_by = prices_module.Config.HOLDINGS_PRICE_MAX_STALENESS_SECONDS + 60
    payload = {
        "ethereum": {"usd": 2500.0, "last_updated_at": _ts(stale_by)},
        "usd-coin": {"usd": 1.0, "last_updated_at": _ts(10)},
    }
    with patch.object(prices_module.requests, "get", return_value=_http_response(payload)):
        result = get_usd_prices(["ethereum", "usd-coin"])

    assert "ethereum" not in result
    assert set(result) == {"usd-coin"}


def test_missing_id_is_omitted_not_defaulted(sb):
    payload = {"ethereum": {"usd": 2500.0, "last_updated_at": _ts(10)}}
    with patch.object(prices_module.requests, "get", return_value=_http_response(payload)):
        result = get_usd_prices(["ethereum", "no-such-coin"])

    assert set(result) == {"ethereum"}
    assert "no-such-coin" not in result


def test_entry_without_timestamp_is_omitted(sb):
    """No last_updated_at means we cannot prove freshness, so we cannot use it."""
    payload = {
        "ethereum": {"usd": 2500.0},
        "usd-coin": {"usd": 1.0, "last_updated_at": _ts(5)},
    }
    with patch.object(prices_module.requests, "get", return_value=_http_response(payload)):
        result = get_usd_prices(["ethereum", "usd-coin"])

    assert set(result) == {"usd-coin"}


@pytest.mark.parametrize("bad_price", [0, -1, "abc", None])
def test_non_positive_or_unparseable_price_is_omitted(sb, bad_price):
    payload = {"ethereum": {"usd": bad_price, "last_updated_at": _ts(5)}}
    with patch.object(prices_module.requests, "get", return_value=_http_response(payload)):
        result = get_usd_prices(["ethereum"])

    assert result == {}


def test_cache_hit_path_skips_the_http_call(sb):
    payload = {"ethereum": {"usd": 2500.0, "last_updated_at": _ts(10)}}
    with patch.object(prices_module.requests, "get", return_value=_http_response(payload)) as get:
        first = get_usd_prices(["ethereum"])
        second = get_usd_prices(["ethereum"])

    assert get.call_count == 1
    assert first == second
    assert second["ethereum"].price == Decimal("2500")


def test_only_uncached_ids_are_requested(sb):
    first_payload = {"ethereum": {"usd": 2500.0, "last_updated_at": _ts(10)}}
    second_payload = {"usd-coin": {"usd": 1.0, "last_updated_at": _ts(10)}}
    with patch.object(
        prices_module.requests,
        "get",
        side_effect=[_http_response(first_payload), _http_response(second_payload)],
    ) as get:
        get_usd_prices(["ethereum"])
        result = get_usd_prices(["ethereum", "usd-coin"])

    assert get.call_count == 2
    # The second call asked only for the id that was not already cached.
    assert get.call_args_list[1].kwargs["params"]["ids"] == "usd-coin"
    assert set(result) == {"ethereum", "usd-coin"}


def test_cached_entry_that_has_gone_stale_is_not_served(sb):
    """A cache hit is still subject to the staleness contract."""
    max_age = prices_module.Config.HOLDINGS_PRICE_MAX_STALENESS_SECONDS
    stale_point = PricePoint(
        price=Decimal("2500"),
        as_of=datetime.now(UTC) - timedelta(seconds=max_age + 60),
    )
    prices_module._local_cache_set("ethereum", stale_point)

    payload = {"ethereum": {"usd": 2600.0, "last_updated_at": _ts(5)}}
    with patch.object(prices_module.requests, "get", return_value=_http_response(payload)) as get:
        result = get_usd_prices(["ethereum"])

    assert get.call_count == 1
    assert result["ethereum"].price == Decimal("2600")


def test_network_error_returns_no_prices_rather_than_guesses(sb):
    with patch.object(
        prices_module.requests, "get", side_effect=ConnectionError("coingecko unreachable")
    ):
        result = get_usd_prices(["ethereum", "usd-coin"])

    assert result == {}


def test_network_error_still_serves_fresh_cached_prices(sb):
    prices_module._local_cache_set(
        "ethereum", PricePoint(price=Decimal("2500"), as_of=datetime.now(UTC))
    )
    with patch.object(
        prices_module.requests, "get", side_effect=ConnectionError("coingecko unreachable")
    ):
        result = get_usd_prices(["ethereum", "usd-coin"])

    assert set(result) == {"ethereum"}


def test_malformed_payload_returns_no_prices(sb):
    with patch.object(
        prices_module.requests, "get", return_value=_http_response(["not", "a", "dict"])
    ):
        assert get_usd_prices(["ethereum"]) == {}


def test_empty_input_makes_no_http_call(sb):
    with patch.object(prices_module.requests, "get") as get:
        assert get_usd_prices([]) == {}
    get.assert_not_called()


def test_blank_and_duplicate_ids_are_normalised(sb):
    payload = {"ethereum": {"usd": 2500.0, "last_updated_at": _ts(5)}}
    with patch.object(prices_module.requests, "get", return_value=_http_response(payload)) as get:
        result = get_usd_prices([" ethereum ", "ethereum", "", "   "])

    assert get.call_args.kwargs["params"]["ids"] == "ethereum"
    assert set(result) == {"ethereum"}


def test_redis_cache_round_trip(sb):
    """When Redis is available it is used instead of the in-process cache."""
    store: dict[str, str] = {}
    fake_redis = MagicMock()
    fake_redis.get.side_effect = store.get
    fake_redis.setex.side_effect = lambda key, ttl, value: store.__setitem__(key, value)

    payload = {"ethereum": {"usd": 2500.0, "last_updated_at": _ts(10)}}
    with patch.object(prices_module, "_redis_client", return_value=fake_redis):
        with patch.object(
            prices_module.requests, "get", return_value=_http_response(payload)
        ) as get:
            first = get_usd_prices(["ethereum"])
            second = get_usd_prices(["ethereum"])

    assert get.call_count == 1
    assert first["ethereum"].price == second["ethereum"].price == Decimal("2500")
    assert fake_redis.setex.called


def test_redis_failure_falls_back_to_the_network(sb):
    fake_redis = MagicMock()
    fake_redis.get.side_effect = RuntimeError("redis down")
    fake_redis.setex.side_effect = RuntimeError("redis down")

    payload = {"ethereum": {"usd": 2500.0, "last_updated_at": _ts(10)}}
    with patch.object(prices_module, "_redis_client", return_value=fake_redis):
        with patch.object(prices_module.requests, "get", return_value=_http_response(payload)):
            result = get_usd_prices(["ethereum"])

    assert result["ethereum"].price == Decimal("2500")
