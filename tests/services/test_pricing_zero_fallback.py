"""A 0/0 database price must not shadow the manual JSON price.

Providers whose API publishes no prices (Groq) sync a 0/0 row. Before this fix
the lookup stopped at that row, so every Groq model showed $0 and failed the
servability gate even though manual_pricing.json priced them. Also covers the
nested-namespace id match ("groq/openai/gpt-oss-120b" -> "groq/gpt-oss-120b"),
``:free`` models keeping their zero price, and the provider fetch loader
preferring the in-code map over stale DB module paths.

All DB access is mocked.
"""

from unittest.mock import MagicMock, patch

from src.services.pricing.pricing_lookup import (
    _is_all_zero_price,
    _resolve_pricing_from_db,
    enrich_model_with_pricing,
    get_all_pricing_batch,
    get_model_pricing,
)


def _client_returning(rows):
    """A Supabase client whose every select chain returns *rows*."""
    result = MagicMock()
    result.data = rows
    chain = MagicMock()
    for name in ("table", "select", "eq", "range", "limit"):
        getattr(chain, name).return_value = chain
    chain.execute.return_value = result
    return chain


# --------------------------------------------------------------------------
# _is_all_zero_price
# --------------------------------------------------------------------------


def test_all_zero_variants_count_as_missing():
    assert _is_all_zero_price(0, 0)
    assert _is_all_zero_price("0", "0.0")
    assert _is_all_zero_price(None, "0")
    assert _is_all_zero_price("", None)


def test_any_nonzero_counts_as_priced():
    assert not _is_all_zero_price("0", "0.000002")
    assert not _is_all_zero_price("1e-6", 0)


# --------------------------------------------------------------------------
# get_all_pricing_batch
# --------------------------------------------------------------------------


def test_batch_skips_zero_rows_but_keeps_priced_and_free():
    rows = [
        {
            "provider_model_id": "groq/llama-3.3-70b-versatile",
            "metadata": {"pricing_raw": {"prompt": "0.0", "completion": "0.0"}},
            "model_pricing": [],
        },
        {
            "provider_model_id": "groq/zero-join",
            "metadata": {},
            "model_pricing": [{"price_per_input_token": "0", "price_per_output_token": "0"}],
        },
        {
            "provider_model_id": "openai/gpt-4o-mini",
            "metadata": {"pricing_raw": {"prompt": "0.00000015", "completion": "0.0000006"}},
            "model_pricing": [],
        },
        {
            "provider_model_id": "vendor/model:free",
            "metadata": {"pricing_raw": {"prompt": "0", "completion": "0"}},
            "model_pricing": [],
        },
    ]
    with patch(
        "src.config.supabase_config.get_supabase_client", return_value=_client_returning(rows)
    ):
        batch = get_all_pricing_batch()

    assert "groq/llama-3.3-70b-versatile" not in batch
    assert "groq/zero-join" not in batch
    assert "openai/gpt-4o-mini" in batch
    assert "vendor/model:free" in batch


# --------------------------------------------------------------------------
# _resolve_pricing_from_db (shared by display and billing)
# --------------------------------------------------------------------------


def test_resolver_returns_none_for_zero_row():
    rows = [
        {
            "id": 1,
            "model_name": "x",
            "metadata": {"pricing_raw": {"prompt": "0", "completion": "0"}},
        }
    ]
    with patch(
        "src.config.supabase_config.get_supabase_client", return_value=_client_returning(rows)
    ):
        assert _resolve_pricing_from_db("groq/llama-3.3-70b-versatile") is None


def test_resolver_keeps_zero_row_for_free_model():
    rows = [
        {
            "id": 1,
            "model_name": "x",
            "metadata": {"pricing_raw": {"prompt": "0", "completion": "0"}},
        }
    ]
    with patch(
        "src.config.supabase_config.get_supabase_client", return_value=_client_returning(rows)
    ):
        result = _resolve_pricing_from_db("vendor/model:free")
    assert result is not None
    assert float(result["prompt"]) == 0.0


def test_resolver_returns_real_price():
    rows = [
        {
            "id": 1,
            "model_name": "x",
            "metadata": {"pricing_raw": {"prompt": "0.00000015", "completion": "0.0000006"}},
        }
    ]
    with patch(
        "src.config.supabase_config.get_supabase_client", return_value=_client_returning(rows)
    ):
        result = _resolve_pricing_from_db("openai/gpt-4o-mini")
    assert result is not None
    assert float(result["prompt"]) > 0


# --------------------------------------------------------------------------
# enrich_model_with_pricing — falls through to manual JSON
# --------------------------------------------------------------------------


def test_zero_db_row_falls_back_to_manual_price():
    # The batch omits zero rows now, so the manual tier is reached.
    model = {
        "id": "groq/llama-3.3-70b-versatile",
        "pricing": {"prompt": "0.0", "completion": "0.0"},
    }
    result = enrich_model_with_pricing(model, "groq", pricing_batch={}, openrouter_index={})
    assert result is not None
    assert result["pricing_source"] == "manual"
    assert float(result["pricing"]["prompt"]) > 0
    assert float(result["pricing"]["completion"]) > 0


def test_per_model_zero_row_falls_back_to_manual_price():
    rows = [
        {
            "id": 1,
            "model_name": "x",
            "metadata": {"pricing_raw": {"prompt": "0", "completion": "0"}},
        }
    ]
    model = {"id": "groq/llama-3.3-70b-versatile"}
    with patch(
        "src.config.supabase_config.get_supabase_client", return_value=_client_returning(rows)
    ):
        result = enrich_model_with_pricing(model, "groq", openrouter_index={})
    assert result is not None
    assert result["pricing_source"] == "manual"


# --------------------------------------------------------------------------
# get_model_pricing — nested namespace
# --------------------------------------------------------------------------


def test_nested_namespace_id_resolves_to_manual_entry():
    nested = get_model_pricing("groq", "groq/openai/gpt-oss-120b")
    flat = get_model_pricing("groq", "groq/gpt-oss-120b")
    assert flat is not None
    assert nested == flat


def test_exact_match_still_wins():
    assert get_model_pricing("groq", "groq/llama-3.1-8b-instant") is not None


def test_unknown_nested_id_stays_unpriced():
    assert get_model_pricing("groq", "groq/acme/not-a-model") is None


# --------------------------------------------------------------------------
# dynamic provider loader — stale DB module paths
# --------------------------------------------------------------------------


def test_fetch_loader_prefers_in_code_map_over_stale_db_path():
    from src.services import dynamic_provider_loader as loader
    from src.services.providers.zai_catalog import fetch_models_from_zai

    loader.invalidate_loader_cache()
    with patch.object(loader, "_resolve_from_registry") as from_registry:
        func = loader.get_fetch_models_function("zai")
    loader.invalidate_loader_cache()

    assert func is fetch_models_from_zai
    from_registry.assert_not_called()


def test_fetch_loader_still_uses_db_for_unmapped_slug():
    from src.services import dynamic_provider_loader as loader

    sentinel = MagicMock()
    loader.invalidate_loader_cache()
    with patch.object(loader, "_resolve_from_registry", return_value=sentinel):
        func = loader.get_fetch_models_function("some-db-only-provider")
    loader.invalidate_loader_cache()

    assert func is sentinel
