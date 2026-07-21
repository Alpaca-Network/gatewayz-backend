"""Nightly pricing-drift monitor: catches billing below provider cost even with markup.

Mocks the catalog snapshot (get_active_provider_slugs / get_models_by_provider_slug /
_resolve_pricing_from_db) and the OpenRouter reference lookup
(_get_cross_reference_pricing) so the test is fast and deterministic — no real DB or
network calls.
"""

from unittest.mock import patch

from src.services.billing import pricing_drift_monitor as drift_mod

MODULE = "src.services.billing.pricing_drift_monitor"


def _model(provider_model_id: str, model_name: str | None = None) -> dict:
    return {"provider_model_id": provider_model_id, "model_name": model_name or provider_model_id}


def _run(
    provider_slugs,
    models_by_provider,
    our_pricing_by_model,
    ref_pricing_by_model,
    markup=1.03,
):
    """Wire up mocks and call audit_pricing_drift(), returning the report dict."""

    def fake_get_models_by_provider_slug(provider_slug, is_active_only=True):
        return models_by_provider.get(provider_slug, [])

    def fake_resolve_pricing(model_id, candidate_ids=None):
        return our_pricing_by_model.get(model_id)

    def fake_cross_reference(model_id, openrouter_index=None, provider=None):
        return ref_pricing_by_model.get(model_id)

    with (
        patch(f"{MODULE}.Config") as mock_config,
        patch(f"{MODULE}.get_active_provider_slugs", return_value=provider_slugs),
        patch(
            f"{MODULE}.get_models_by_provider_slug", side_effect=fake_get_models_by_provider_slug
        ),
        patch(f"{MODULE}._resolve_pricing_from_db", side_effect=fake_resolve_pricing),
        patch(f"{MODULE}._build_openrouter_pricing_index", return_value={"stub": True}),
        patch(f"{MODULE}._get_cross_reference_pricing", side_effect=fake_cross_reference),
    ):
        mock_config.PRICING_MARKUP = markup
        return drift_mod.audit_pricing_drift()


def test_below_cost_model_is_flagged_as_drift():
    """catalog_price * markup still below reference cost => DRIFT."""
    model_id = "meta-llama/Llama-3-8b"
    report = _run(
        provider_slugs=["featherless"],
        models_by_provider={"featherless": [_model(model_id)]},
        our_pricing_by_model={model_id: {"prompt": "0.0000009", "completion": "0.0000009"}},
        ref_pricing_by_model={model_id: {"prompt": "0.000001", "completion": "0.000001"}},
        markup=1.03,
    )

    assert report["checked"] == 1
    assert not report["ok"]
    assert len(report["drift"]) == 1
    flagged = report["drift"][0]
    assert flagged["model_id"] == model_id
    assert flagged["provider"] == "featherless"
    assert flagged["deficit_pct"] > 0
    assert report["worst_deficit_pct"] == flagged["deficit_pct"]
    assert report["unpriced"] == []


def test_at_or_above_cost_model_is_not_flagged():
    """catalog_price * markup >= reference cost => no drift."""
    model_id = "meta-llama/Llama-3-70b"
    report = _run(
        provider_slugs=["featherless"],
        models_by_provider={"featherless": [_model(model_id)]},
        our_pricing_by_model={model_id: {"prompt": "0.000001", "completion": "0.000001"}},
        ref_pricing_by_model={model_id: {"prompt": "0.000001", "completion": "0.000001"}},
        markup=1.03,
    )

    assert report["checked"] == 1
    assert report["ok"]
    assert report["drift"] == []
    assert report["unpriced"] == []
    assert report["worst_deficit_pct"] == 0.0


def test_unpriced_active_model_is_flagged():
    """Active model with no catalog price (None) => flagged as unpriced, not drift."""
    model_id = "some-org/unpriced-model"
    report = _run(
        provider_slugs=["deepinfra"],
        models_by_provider={"deepinfra": [_model(model_id)]},
        our_pricing_by_model={},  # _resolve_pricing_from_db returns None
        ref_pricing_by_model={model_id: {"prompt": "0.000001", "completion": "0.000001"}},
        markup=1.03,
    )

    assert report["checked"] == 1
    assert not report["ok"]
    assert report["drift"] == []
    assert len(report["unpriced"]) == 1
    assert report["unpriced"][0]["model_id"] == model_id
    assert report["unpriced"][0]["provider"] == "deepinfra"


def test_zero_priced_model_is_flagged_as_unpriced():
    """Active model priced at exactly 0 on both sides => unpriced, not drift (no
    deficit % of nothing)."""
    model_id = "some-org/zero-priced-model"
    report = _run(
        provider_slugs=["deepinfra"],
        models_by_provider={"deepinfra": [_model(model_id)]},
        our_pricing_by_model={model_id: {"prompt": "0", "completion": "0"}},
        ref_pricing_by_model={model_id: {"prompt": "0.000001", "completion": "0.000001"}},
        markup=1.03,
    )

    assert not report["ok"]
    assert report["drift"] == []
    assert len(report["unpriced"]) == 1


def test_markup_is_applied_in_drift_threshold():
    """Without markup, catalog price is below reference; WITH markup applied it
    clears the reference — must NOT be flagged. Proves markup is actually applied
    (not just compared raw catalog price to catalog price)."""
    model_id = "org/markup-saved-model"
    our_price = 0.98e-6
    ref_price = 1.0e-6
    assert our_price < ref_price  # raw catalog price alone would look like drift
    markup = 1.03
    assert our_price * markup > ref_price  # but markup clears the reference cost

    report = _run(
        provider_slugs=["featherless"],
        models_by_provider={"featherless": [_model(model_id)]},
        our_pricing_by_model={model_id: {"prompt": str(our_price), "completion": str(our_price)}},
        ref_pricing_by_model={model_id: {"prompt": str(ref_price), "completion": str(ref_price)}},
        markup=markup,
    )

    assert report["ok"]
    assert report["drift"] == []

    # Sanity: with a lower markup that does NOT clear the reference, it IS flagged.
    report_no_markup = _run(
        provider_slugs=["featherless"],
        models_by_provider={"featherless": [_model(model_id)]},
        our_pricing_by_model={model_id: {"prompt": str(our_price), "completion": str(our_price)}},
        ref_pricing_by_model={model_id: {"prompt": str(ref_price), "completion": str(ref_price)}},
        markup=1.0,
    )
    assert not report_no_markup["ok"]
    assert len(report_no_markup["drift"]) == 1


def test_openrouter_provider_is_never_compared_against_itself():
    """OpenRouter IS the reference catalog — its own models should never be
    cross-checked (and thus never flagged as drift, regardless of price)."""
    model_id = "openrouter/some-model"
    report = _run(
        provider_slugs=["openrouter"],
        models_by_provider={"openrouter": [_model(model_id)]},
        our_pricing_by_model={model_id: {"prompt": "0.0000001", "completion": "0.0000001"}},
        ref_pricing_by_model={model_id: {"prompt": "0.000001", "completion": "0.000001"}},
        markup=1.03,
    )

    assert report["checked"] == 1
    assert report["ok"]
    assert report["drift"] == []


def test_multiple_providers_sorted_worst_first():
    """Drift list must be sorted worst deficit first across multiple providers."""
    model_a = "org/model-a"  # small deficit
    model_b = "org/model-b"  # large deficit

    report = _run(
        provider_slugs=["featherless", "deepinfra"],
        models_by_provider={
            "featherless": [_model(model_a)],
            "deepinfra": [_model(model_b)],
        },
        our_pricing_by_model={
            model_a: {"prompt": "0.00000099", "completion": "0.00000099"},  # tiny deficit
            model_b: {"prompt": "0.0000005", "completion": "0.0000005"},  # huge deficit
        },
        ref_pricing_by_model={
            model_a: {"prompt": "0.000001", "completion": "0.000001"},
            model_b: {"prompt": "0.000001", "completion": "0.000001"},
        },
        markup=1.0,
    )

    assert len(report["drift"]) == 2
    assert report["drift"][0]["model_id"] == model_b
    assert report["drift"][1]["model_id"] == model_a
    assert report["drift"][0]["deficit_pct"] >= report["drift"][1]["deficit_pct"]
