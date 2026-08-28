"""Catalog servability annotation (issue #2236).

The catalog listed xai/moonshot models ``is_active: true`` while the pricing
gate refused them at inference time — the catalog lied about servability and a
partner nearly synced a registry from it. Every served model row now carries a
``servable`` flag derived from the same signal the gate uses: pricing
configured (nonzero prompt or completion price) or explicitly free.
"""

from src.routes.catalog import _annotate_servability, _row_is_servable


class TestRowIsServable:
    def test_priced_model_is_servable(self):
        assert _row_is_servable({"id": "openai/gpt-4o", "pricing": {"prompt": "0.0000025", "completion": "0.00001"}}) is True

    def test_missing_pricing_is_not_servable(self):
        assert _row_is_servable({"id": "xai/grok-4"}) is False

    def test_empty_pricing_is_not_servable(self):
        assert _row_is_servable({"id": "moonshot/kimi-k2", "pricing": {}}) is False

    def test_zero_priced_paid_model_is_not_servable(self):
        assert _row_is_servable({"id": "xai/grok-4", "pricing": {"prompt": "0", "completion": "0"}}) is False

    def test_free_flag_is_servable(self):
        assert _row_is_servable({"id": "some/model", "is_free": True}) is True

    def test_free_suffix_is_servable(self):
        assert _row_is_servable({"id": "meta-llama/llama-3:free", "pricing": {}}) is True

    def test_garbage_pricing_values_fail_closed(self):
        assert _row_is_servable({"id": "m", "pricing": {"prompt": "n/a", "completion": None}}) is False

    def test_unique_models_shape_any_priced_provider(self):
        row = {
            "id": "gpt-4o",
            "providers": [
                {"slug": "a", "pricing": {"prompt": "0", "completion": "0"}},
                {"slug": "b", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
            ],
        }
        assert _row_is_servable(row) is True

    def test_unique_models_shape_no_priced_provider(self):
        row = {"id": "x", "providers": [{"slug": "a", "pricing": {}}]}
        assert _row_is_servable(row) is False


class TestAnnotateServability:
    def test_annotates_every_dict_row(self):
        models = [
            {"id": "a", "pricing": {"prompt": "0.001", "completion": "0.002"}},
            {"id": "b", "pricing": {}},
            "not-a-dict",
        ]
        out = _annotate_servability(models)
        assert out[0]["servable"] is True
        assert out[1]["servable"] is False
        assert out[2] == "not-a-dict"

    def test_returns_input_on_empty(self):
        assert _annotate_servability([]) == []
