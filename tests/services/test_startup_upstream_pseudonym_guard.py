"""Startup guard for the upstream abuse-pseudonym mode (fix round 1).

Config.UPSTREAM_PSEUDONYM_SECRET is only read at request time, inside
scrub_upstream_kwargs() -- if UPSTREAM_ABUSE_PSEUDONYM is turned on with the
secret missing/short, that would otherwise raise on every chat completion
instead of failing loudly once at startup. See
src/services/startup.py's _validate_upstream_pseudonym_config, called from
the app's lifespan handler before it accepts any traffic.
"""

from types import SimpleNamespace

import pytest

from src.services.startup import _validate_upstream_pseudonym_config


def _config(*, enabled: bool, secret: str | None):
    return SimpleNamespace(UPSTREAM_ABUSE_PSEUDONYM=enabled, UPSTREAM_PSEUDONYM_SECRET=secret)


class TestValidateUpstreamPseudonymConfig:
    def test_disabled_is_a_noop_even_without_a_secret(self):
        _validate_upstream_pseudonym_config(_config(enabled=False, secret=None))

    def test_enabled_with_missing_secret_raises(self):
        with pytest.raises(RuntimeError, match="UPSTREAM_PSEUDONYM_SECRET"):
            _validate_upstream_pseudonym_config(_config(enabled=True, secret=None))

    def test_enabled_with_empty_secret_raises(self):
        with pytest.raises(RuntimeError, match="UPSTREAM_PSEUDONYM_SECRET"):
            _validate_upstream_pseudonym_config(_config(enabled=True, secret=""))

    def test_enabled_with_short_secret_raises(self):
        with pytest.raises(RuntimeError, match="UPSTREAM_PSEUDONYM_SECRET"):
            _validate_upstream_pseudonym_config(_config(enabled=True, secret="short-secret"))

    def test_enabled_with_31_char_secret_still_raises(self):
        """Boundary: one character short of the 32-char floor must still fail."""
        with pytest.raises(RuntimeError):
            _validate_upstream_pseudonym_config(_config(enabled=True, secret="a" * 31))

    def test_enabled_with_32_char_secret_passes(self):
        _validate_upstream_pseudonym_config(_config(enabled=True, secret="a" * 32))

    def test_enabled_with_long_secret_passes(self):
        _validate_upstream_pseudonym_config(_config(enabled=True, secret="a" * 64))


class TestLifespanCallsTheGuard:
    def test_lifespan_source_calls_validate_upstream_pseudonym_config(self):
        """Structural guard: the lifespan startup sequence must actually call
        the validator (not just define it) -- mirrors the AST-based
        call-site conventions used elsewhere in this suite
        (tests/security/test_upstream_identity_firewall.py).
        """
        import ast
        from pathlib import Path

        startup_py = Path(__file__).resolve().parents[2] / "src" / "services" / "startup.py"
        tree = ast.parse(startup_py.read_text())
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_validate_upstream_pseudonym_config"
        ]
        assert calls, "_validate_upstream_pseudonym_config must be called from lifespan()"
