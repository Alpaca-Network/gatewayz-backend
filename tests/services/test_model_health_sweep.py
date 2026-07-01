"""
Pure unit tests for the model health sweep classifier and the catalog health
gating filter. No DB / network — classification is pure and gating is fed plain
dicts that all carry an inline ``health_status`` (so the DB down-set is never
consulted).
"""

from __future__ import annotations

import pytest

from src.services.monitoring.model_health_sweep import (
    HARD_FAIL_THRESHOLD,
    classify_probe_result,
)


class TestClassifyProbeResult:
    def test_pass_is_healthy(self):
        assert classify_probe_result("pass", 200, None) == "healthy"

    def test_skip_is_skip(self):
        assert classify_probe_result("skip", None, "non-chat modality") == "skip"

    def test_fail_404_is_hard_fail(self):
        assert classify_probe_result("fail", 404, "HTTP 404: model missing") == "hard_fail"

    def test_fail_429_is_soft(self):
        assert classify_probe_result("fail", 429, "HTTP 429: slow down") == "soft"

    def test_fail_401_is_soft(self):
        assert classify_probe_result("fail", 401, "HTTP 401: bad key") == "soft"

    def test_fail_403_is_soft(self):
        assert classify_probe_result("fail", 403, "HTTP 403: forbidden") == "soft"

    def test_fail_503_is_hard_fail(self):
        assert classify_probe_result("fail", 503, "HTTP 503: upstream error") == "hard_fail"

    def test_fail_500_is_hard_fail(self):
        assert classify_probe_result("fail", 500, "HTTP 500: server error") == "hard_fail"

    def test_timeout_is_soft(self):
        assert classify_probe_result("timeout", None, "Timed out after 60s") == "soft"

    def test_fail_no_endpoints_text_is_hard_fail(self):
        # No status code, but the error text clearly indicates a dead model.
        assert classify_probe_result("fail", None, "No endpoints found for model X") == "hard_fail"

    def test_fail_no_allowed_providers_is_hard_fail(self):
        assert (
            classify_probe_result("fail", 400, "No allowed providers are available") == "hard_fail"
        )

    def test_ambiguous_fail_is_soft(self):
        # 400 with a generic message → soft (never hide on ambiguous evidence).
        assert classify_probe_result("fail", 400, "Bad request: invalid params") == "soft"

    def test_error_with_not_found_text_is_hard_fail(self):
        assert classify_probe_result("error", None, "model does not exist") == "hard_fail"

    def test_generic_error_is_soft(self):
        assert classify_probe_result("error", None, "Connection reset by peer") == "soft"

    def test_rate_limit_text_without_code_is_soft(self):
        assert classify_probe_result("fail", None, "rate limit exceeded") == "soft"

    def test_unknown_status_is_soft(self):
        assert classify_probe_result("weird", None, None) == "soft"

    def test_hard_fail_threshold_constant(self):
        assert HARD_FAIL_THRESHOLD == 3


class TestApplyHealthGating:
    @pytest.fixture
    def models(self):
        return [
            {"id": "openai/gpt-x", "health_status": "healthy"},
            {"id": "dead/model", "health_status": "down"},
            {"id": "meta/unknown", "health_status": "unknown"},
            {"id": "no/status"},  # missing health_status entirely
        ]

    def test_flag_on_removes_only_down(self, models, monkeypatch):
        from src.config.config import Config
        from src.routes import catalog

        monkeypatch.setattr(Config, "HEALTH_GATING_ENABLED", True)
        # Force the down-set (used only for the model lacking health_status) empty
        # so the test never touches the DB.
        monkeypatch.setattr(catalog, "_get_down_model_id_set", lambda: set())

        result = catalog._apply_health_gating(list(models))
        ids = [m["id"] for m in result]

        assert "dead/model" not in ids
        assert ids == ["openai/gpt-x", "meta/unknown", "no/status"]

    def test_flag_off_removes_nothing(self, models, monkeypatch):
        from src.config.config import Config
        from src.routes import catalog

        monkeypatch.setattr(Config, "HEALTH_GATING_ENABLED", False)

        result = catalog._apply_health_gating(list(models))
        assert len(result) == len(models)
        assert [m["id"] for m in result] == [m["id"] for m in models]

    def test_flag_on_drops_model_in_down_set(self, monkeypatch):
        from src.config.config import Config
        from src.routes import catalog

        monkeypatch.setattr(Config, "HEALTH_GATING_ENABLED", True)
        # A served model lacking inline health_status but present in the down-set
        # must be dropped.
        monkeypatch.setattr(catalog, "_get_down_model_id_set", lambda: {"no/status"})

        models = [
            {"id": "openai/gpt-x", "health_status": "healthy"},
            {"id": "no/status"},
        ]
        result = catalog._apply_health_gating(models)
        assert [m["id"] for m in result] == ["openai/gpt-x"]

    def test_empty_input_returns_empty(self, monkeypatch):
        from src.config.config import Config
        from src.routes import catalog

        monkeypatch.setattr(Config, "HEALTH_GATING_ENABLED", True)
        assert catalog._apply_health_gating([]) == []
