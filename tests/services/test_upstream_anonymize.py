"""Unit tests for the upstream identity firewall (docs/security/ANONYMITY_THREAT_MODEL.md G1).

The functional proof that scrubbing actually reaches every provider client is
tests/security/test_upstream_identity_firewall.py -- this file only covers
scrub_upstream_kwargs/pseudonym in isolation.
"""

import pytest

from src.config import Config
from src.services.upstream.anonymize import (
    OUTBOUND_DENY_FIELDS,
    pseudonym,
    scrub_upstream_kwargs,
)


class TestScrubUpstreamKwargs:
    def test_drops_user_field(self):
        assert "user" not in scrub_upstream_kwargs({"user": "alice@example.com", "model": "m"})

    def test_drops_all_deny_listed_fields(self):
        kwargs = {
            "user": "alice",
            "metadata": {"user_id": "alice"},
            "extra_body": {"x": 1},
            "extra_headers": {"X-User": "alice"},
            "extra_query": {"user": "alice"},
            "temperature": 0.5,
        }
        scrubbed = scrub_upstream_kwargs(kwargs)
        assert scrubbed == {"temperature": 0.5}
        assert set(scrubbed) & OUTBOUND_DENY_FIELDS == set()

    def test_leaves_non_identity_fields_untouched(self):
        kwargs = {"temperature": 0.7, "max_tokens": 100, "tools": [{"type": "function"}]}
        assert scrub_upstream_kwargs(kwargs) == kwargs

    def test_does_not_mutate_input(self):
        kwargs = {"user": "alice", "model": "m"}
        original = dict(kwargs)
        scrub_upstream_kwargs(kwargs)
        assert kwargs == original

    def test_empty_dict(self):
        assert scrub_upstream_kwargs({}) == {}

    def test_pseudonym_off_by_default_leaves_user_absent(self, monkeypatch):
        monkeypatch.setattr(Config, "UPSTREAM_ABUSE_PSEUDONYM", False, raising=False)
        scrubbed = scrub_upstream_kwargs({"user": "alice"}, billing_ref="ref-1")
        assert "user" not in scrubbed

    def test_pseudonym_on_without_billing_ref_leaves_user_absent(self, monkeypatch):
        monkeypatch.setattr(Config, "UPSTREAM_ABUSE_PSEUDONYM", True, raising=False)
        monkeypatch.setattr(Config, "UPSTREAM_PSEUDONYM_SECRET", "s3cr3t", raising=False)
        scrubbed = scrub_upstream_kwargs({"user": "alice"}, billing_ref=None)
        assert "user" not in scrubbed

    def test_pseudonym_on_with_billing_ref_sets_pseudonym_not_client_value(self, monkeypatch):
        monkeypatch.setattr(Config, "UPSTREAM_ABUSE_PSEUDONYM", True, raising=False)
        monkeypatch.setattr(Config, "UPSTREAM_PSEUDONYM_SECRET", "s3cr3t", raising=False)
        scrubbed = scrub_upstream_kwargs({"user": "alice"}, billing_ref="ref-1")
        assert scrubbed["user"] == pseudonym("ref-1")
        assert scrubbed["user"] != "alice"


class TestPseudonym:
    def test_deterministic_for_same_ref(self, monkeypatch):
        monkeypatch.setattr(Config, "UPSTREAM_PSEUDONYM_SECRET", "s3cr3t", raising=False)
        assert pseudonym("ref-1") == pseudonym("ref-1")

    def test_different_refs_produce_different_pseudonyms(self, monkeypatch):
        monkeypatch.setattr(Config, "UPSTREAM_PSEUDONYM_SECRET", "s3cr3t", raising=False)
        assert pseudonym("ref-1") != pseudonym("ref-2")

    def test_not_derivable_from_billing_ref_alone(self, monkeypatch):
        """Different secrets must produce different pseudonyms for the same ref --
        the pseudonym is a function of the secret, not just the ref."""
        monkeypatch.setattr(Config, "UPSTREAM_PSEUDONYM_SECRET", "secret-a", raising=False)
        a = pseudonym("ref-1")
        monkeypatch.setattr(Config, "UPSTREAM_PSEUDONYM_SECRET", "secret-b", raising=False)
        b = pseudonym("ref-1")
        assert a != b

    def test_has_gw_prefix(self, monkeypatch):
        monkeypatch.setattr(Config, "UPSTREAM_PSEUDONYM_SECRET", "s3cr3t", raising=False)
        assert pseudonym("ref-1").startswith("gw_")

    def test_raises_clear_error_when_secret_unset(self, monkeypatch):
        monkeypatch.setattr(Config, "UPSTREAM_PSEUDONYM_SECRET", None, raising=False)
        with pytest.raises(ValueError, match="UPSTREAM_PSEUDONYM_SECRET"):
            pseudonym("ref-1")

    def test_does_not_contain_billing_ref_as_substring(self, monkeypatch):
        monkeypatch.setattr(Config, "UPSTREAM_PSEUDONYM_SECRET", "s3cr3t", raising=False)
        billing_ref = "canary-req-424242"
        assert billing_ref not in pseudonym(billing_ref)
