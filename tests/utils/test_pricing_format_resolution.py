"""A provider's pricing units must resolve whether or not we route to it.

get_provider_format read the gateway registry, which is built from ACTIVE
providers and then filtered again by ENABLED_PROVIDERS. A disabled provider was
absent from both, fell through to the PER_1M default, and had every price
divided by a million.

That is not hypothetical. OpenRouter declares per_token and publishes 0.000005
for a $5/Mtok model. Once it became a price-reference provider, the fallback
turned that into 5E-12, and every model priced from the reference inherited it —
claude-opus-5 landed in production at 5E-18 per token. North Star §4.2 names
this the hardest data-quality problem in the system: a unit error inverts
arbitrage and bills essentially nothing.
"""

import pytest

from src.utils.pricing_normalization import PricingFormat, get_provider_format


@pytest.fixture
def declared(monkeypatch):
    def _set(formats: dict):
        monkeypatch.setattr(
            "src.services.gateway_registry.get_declared_pricing_formats", lambda: formats
        )

    return _set


class TestFormatResolution:
    def test_disabled_provider_still_resolves_its_declared_format(self, declared):
        """OpenRouter is delisted as supply but still quotes per-token prices."""
        declared({"openrouter": "per_token"})

        assert get_provider_format("openrouter") == PricingFormat.PER_TOKEN

    @pytest.mark.parametrize(
        "declared_value,expected",
        [
            ("per_token", PricingFormat.PER_TOKEN),
            ("per_1k", PricingFormat.PER_1K_TOKENS),
            ("per_1m", PricingFormat.PER_1M_TOKENS),
        ],
    )
    def test_each_declared_format_maps_through(self, declared, declared_value, expected):
        declared({"someprovider": declared_value})

        assert get_provider_format("someprovider") == expected

    def test_slug_lookup_is_case_insensitive(self, declared):
        declared({"openrouter": "per_token"})

        assert get_provider_format("OpenRouter") == PricingFormat.PER_TOKEN

    def test_undeclared_provider_falls_back_to_per_1m(self, declared):
        declared({})

        assert get_provider_format("mystery") == PricingFormat.PER_1M_TOKENS

    def test_lookup_failure_falls_back_rather_than_raising(self, monkeypatch):
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr("src.services.gateway_registry.get_declared_pricing_formats", _boom)

        assert get_provider_format("openrouter") == PricingFormat.PER_1M_TOKENS


class TestConversionArithmetic:
    """Guards the actual magnitude, since that is what reached production."""

    def test_per_token_is_not_divided(self):
        from src.utils.pricing_normalization import normalize_to_per_token

        assert float(normalize_to_per_token("0.000005", PricingFormat.PER_TOKEN)) == pytest.approx(
            5e-6
        )

    def test_treating_per_token_as_per_1m_is_the_bug(self):
        """Documents the failure: 5e-6 becomes 5e-12, a million times too cheap."""
        from src.utils.pricing_normalization import normalize_to_per_token

        wrong = float(normalize_to_per_token("0.000005", PricingFormat.PER_1M_TOKENS))

        assert wrong == pytest.approx(5e-12)
        assert wrong != pytest.approx(5e-6)


def test_registry_refresh_also_clears_the_format_cache(monkeypatch):
    """Both caches read the same providers table, so both go stale together.

    Refreshing one without the other would keep serving a unit that has since
    been corrected — the failure this whole change exists to prevent.
    """
    from src.services import gateway_registry

    gateway_registry._pricing_format_cache = {"openrouter": "per_1m"}
    monkeypatch.setattr(gateway_registry, "_load_registry_from_db", lambda: {})

    gateway_registry.refresh_registry_cache()

    assert gateway_registry._pricing_format_cache is None
