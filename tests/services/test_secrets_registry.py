"""Tests for src/services/secrets_registry.py (gatewayz-backend Phase D, D1).

Fingerprint + age registry backing GET /admin/status's `secrets` block --
see src/routes/admin_status.py. Redis + in-process fallback pattern mirrors
src/services/ops/job_runs.py (see tests/services/ops/test_job_runs.py).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.services import secrets_registry


class _FakeRedis:
    """Minimal stateful Redis double supporting hset/hgetall on hash keys --
    enough to prove first_seen_at survives a "restart" (a second call to
    record_secret_fingerprints against the same backing store) without a
    real Redis server."""

    def __init__(self):
        self._hashes: dict[str, dict[str, str]] = {}

    def hset(self, key, mapping=None, **_kwargs):
        self._hashes.setdefault(key, {}).update(mapping or {})
        return len(mapping or {})

    def hgetall(self, key):
        return dict(self._hashes.get(key, {}))


@pytest.fixture(autouse=True)
def _clean_fallback_store():
    secrets_registry._fallback_store.clear()
    yield
    secrets_registry._fallback_store.clear()


class TestFingerprint:
    def test_stable_for_the_same_value(self):
        assert secrets_registry.fingerprint("abc123") == secrets_registry.fingerprint("abc123")

    def test_changes_when_the_value_changes(self):
        assert secrets_registry.fingerprint("abc123") != secrets_registry.fingerprint("xyz789")

    def test_never_contains_the_raw_value(self):
        fp = secrets_registry.fingerprint("super-secret-value")
        assert "super-secret-value" not in fp

    def test_changes_when_the_salt_changes(self, monkeypatch):
        fp_before = secrets_registry.fingerprint("abc123")
        monkeypatch.setenv("SECRET_FP_SALT", "a-different-salt")
        fp_after = secrets_registry.fingerprint("abc123")
        assert fp_before != fp_after


class TestRecordSecretFingerprintsWithRedis:
    def test_first_seen_preserved_across_restarts(self, monkeypatch):
        """Two calls with an unchanged value must not reset first_seen_at --
        this is what "restart the process, secret unchanged" looks like."""
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_unchanged")
        fake_redis = _FakeRedis()
        with patch("src.services.secrets_registry.get_redis_client", return_value=fake_redis):
            secrets_registry.record_secret_fingerprints()
            first_seen_1 = fake_redis.hgetall("ops:secret:STRIPE_SECRET_KEY")["first_seen_at"]

            secrets_registry.record_secret_fingerprints()
            first_seen_2 = fake_redis.hgetall("ops:secret:STRIPE_SECRET_KEY")["first_seen_at"]

        assert first_seen_1 == first_seen_2

    def test_rotation_resets_first_seen_and_logs_name_only(self, monkeypatch, caplog):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_original")
        fake_redis = _FakeRedis()
        with patch("src.services.secrets_registry.get_redis_client", return_value=fake_redis):
            secrets_registry.record_secret_fingerprints()
            first_seen_1 = fake_redis.hgetall("ops:secret:STRIPE_SECRET_KEY")["first_seen_at"]

            monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_rotated")
            with caplog.at_level("INFO", logger="src.services.secrets_registry"):
                secrets_registry.record_secret_fingerprints()
            first_seen_2 = fake_redis.hgetall("ops:secret:STRIPE_SECRET_KEY")["first_seen_at"]

        assert first_seen_1 != first_seen_2
        assert "secret rotated: STRIPE_SECRET_KEY" in caplog.text
        # Never logs the value or a fingerprint.
        assert "sk_test_rotated" not in caplog.text
        assert "sk_test_original" not in caplog.text

    def test_no_rotation_log_on_first_ever_recording(self, monkeypatch, caplog):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_first_time")
        fake_redis = _FakeRedis()
        with (
            patch("src.services.secrets_registry.get_redis_client", return_value=fake_redis),
            caplog.at_level("INFO", logger="src.services.secrets_registry"),
        ):
            secrets_registry.record_secret_fingerprints()

        assert "secret rotated" not in caplog.text


class TestRecordSecretFingerprintsFallback:
    def test_uses_in_process_store_when_redis_unavailable(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.example/1")
        with patch("src.services.secrets_registry.get_redis_client", return_value=None):
            secrets_registry.record_secret_fingerprints()

        assert "SENTRY_DSN" in secrets_registry._fallback_store
        assert secrets_registry._fallback_store["SENTRY_DSN"]["first_seen_at"]

    def test_never_raises_when_get_redis_client_raises(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.example/1")
        with patch(
            "src.services.secrets_registry.get_redis_client",
            side_effect=RuntimeError("no redis configured"),
        ):
            secrets_registry.record_secret_fingerprints()  # must not raise

        assert "SENTRY_DSN" in secrets_registry._fallback_store

    def test_never_raises_when_redis_write_fails(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.example/1")
        broken_redis = MagicMock()
        broken_redis.hgetall.return_value = {}
        broken_redis.hset.side_effect = ConnectionError("redis down")
        with patch("src.services.secrets_registry.get_redis_client", return_value=broken_redis):
            secrets_registry.record_secret_fingerprints()  # must not raise

        assert "SENTRY_DSN" in secrets_registry._fallback_store

    def test_skips_absent_secrets(self, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        with patch("src.services.secrets_registry.get_redis_client", return_value=None):
            secrets_registry.record_secret_fingerprints()

        assert "STRIPE_SECRET_KEY" not in secrets_registry._fallback_store


class TestSecretAges:
    def test_present_but_never_recorded(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_value")
        with patch("src.services.secrets_registry.get_redis_client", return_value=None):
            ages = secrets_registry.secret_ages()

        entry = ages["STRIPE_SECRET_KEY"]
        assert entry["present"] is True
        assert entry["first_seen_at"] is None
        assert entry["age_days"] is None
        assert entry["rotate_due"] is False
        assert entry["fingerprint_known"] is False

    def test_absent_secret(self, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        with patch("src.services.secrets_registry.get_redis_client", return_value=None):
            ages = secrets_registry.secret_ages()

        entry = ages["STRIPE_SECRET_KEY"]
        assert entry["present"] is False
        assert entry["fingerprint_known"] is False

    def test_recorded_secret_past_rotation_window_is_rotate_due(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_value")
        monkeypatch.setenv("SECRET_ROTATION_DAYS", "90")
        old_first_seen = (datetime.now(UTC) - timedelta(days=91)).isoformat()
        secrets_registry._fallback_store["STRIPE_SECRET_KEY"] = {
            "fp": secrets_registry.fingerprint("sk_test_value"),
            "first_seen_at": old_first_seen,
        }
        with patch("src.services.secrets_registry.get_redis_client", return_value=None):
            ages = secrets_registry.secret_ages()

        entry = ages["STRIPE_SECRET_KEY"]
        assert entry["present"] is True
        assert entry["fingerprint_known"] is True
        assert entry["age_days"] >= 91
        assert entry["rotate_due"] is True

    def test_recently_recorded_secret_is_not_rotate_due(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_value")
        recent = datetime.now(UTC).isoformat()
        secrets_registry._fallback_store["STRIPE_SECRET_KEY"] = {
            "fp": secrets_registry.fingerprint("sk_test_value"),
            "first_seen_at": recent,
        }
        with patch("src.services.secrets_registry.get_redis_client", return_value=None):
            ages = secrets_registry.secret_ages()

        assert ages["STRIPE_SECRET_KEY"]["rotate_due"] is False

    def test_never_exposes_the_fingerprint(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_value")
        fp = secrets_registry.fingerprint("sk_test_value")
        secrets_registry._fallback_store["STRIPE_SECRET_KEY"] = {
            "fp": fp,
            "first_seen_at": datetime.now(UTC).isoformat(),
        }
        with patch("src.services.secrets_registry.get_redis_client", return_value=None):
            ages = secrets_registry.secret_ages()

        assert "fp" not in ages["STRIPE_SECRET_KEY"]
        assert fp not in str(ages["STRIPE_SECRET_KEY"])

    def test_lists_every_allowlisted_name(self):
        with patch("src.services.secrets_registry.get_redis_client", return_value=None):
            ages = secrets_registry.secret_ages()

        for name in secrets_registry.SECRET_NAMES:
            assert name in ages
