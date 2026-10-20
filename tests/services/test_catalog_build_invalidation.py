"""Catalog caches are dropped once per deployment.

Redis outlives deploys, so without this a pricing fix only reached
/v1/models after the old build's cached rows expired (up to an hour). After
#2402, /v1/models?gateway=groq kept returning 0/0 "database" rows with
servable=False while an uncached request already priced them.
"""

from unittest.mock import MagicMock, patch

from src.services.cache import catalog_response_cache as crc


def _redis(previous):
    client = MagicMock()
    client.get.return_value = previous
    return client


def test_new_deployment_clears_model_and_response_caches(monkeypatch):
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-new")
    client = _redis(b"dep-old")
    with (
        patch.object(crc, "get_redis_client", return_value=client),
        patch(
            "src.services.cache.model_catalog_cache.clear_all_model_caches", return_value=7
        ) as clear_models,
        patch.object(crc, "invalidate_catalog_cache", return_value=3) as clear_responses,
    ):
        assert crc.invalidate_catalog_caches_for_new_build() is True

    clear_models.assert_called_once_with()
    clear_responses.assert_called_once_with()
    client.set.assert_called_once_with(crc.CATALOG_BUILD_KEY, "dep-new")


def test_same_deployment_restart_keeps_caches(monkeypatch):
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-1")
    client = _redis(b"dep-1")
    with (
        patch.object(crc, "get_redis_client", return_value=client),
        patch("src.services.cache.model_catalog_cache.clear_all_model_caches") as clear_models,
        patch.object(crc, "invalidate_catalog_cache") as clear_responses,
    ):
        assert crc.invalidate_catalog_caches_for_new_build() is False

    clear_models.assert_not_called()
    clear_responses.assert_not_called()
    client.set.assert_not_called()


def test_first_ever_build_clears(monkeypatch):
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-1")
    client = _redis(None)
    with (
        patch.object(crc, "get_redis_client", return_value=client),
        patch("src.services.cache.model_catalog_cache.clear_all_model_caches", return_value=0),
        patch.object(crc, "invalidate_catalog_cache", return_value=0),
    ):
        assert crc.invalidate_catalog_caches_for_new_build() is True
    client.set.assert_called_once_with(crc.CATALOG_BUILD_KEY, "dep-1")


def test_commit_sha_is_the_fallback_build_id(monkeypatch):
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID", raising=False)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")
    assert crc.current_build_id() == "abc123"


def test_no_build_id_outside_railway_is_a_noop(monkeypatch):
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID", raising=False)
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    with patch.object(crc, "get_redis_client") as get_client:
        assert crc.invalidate_catalog_caches_for_new_build() is False
    get_client.assert_not_called()


def test_redis_unavailable_is_a_noop(monkeypatch):
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-1")
    with patch.object(crc, "get_redis_client", return_value=None):
        assert crc.invalidate_catalog_caches_for_new_build() is False


def test_redis_error_never_raises(monkeypatch):
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-1")
    client = MagicMock()
    client.get.side_effect = RuntimeError("redis down")
    with patch.object(crc, "get_redis_client", return_value=client):
        assert crc.invalidate_catalog_caches_for_new_build() is False
