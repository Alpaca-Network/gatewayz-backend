"""Isolation for tests that ``importlib.reload`` config modules.

``reload(src.config.config)`` builds a brand-new ``Config`` class (with whatever
env the test monkeypatched baked in) and rebinds ``config.Config`` to it, while
``src.config.Config`` and every ``from src.config import Config`` holder keep the
old class. monkeypatch only restores the env var afterwards, so the poisoned
class leaked into every later test in the same xdist worker (community routing
tests, /auth Privy tests, ...). Restore the original module attributes after
each test so class identity and values are exactly as they were.
"""

import importlib

import pytest


@pytest.fixture(autouse=True)
def _restore_reloaded_config_modules():
    import src.config as config_pkg
    from src.config import config as config_mod
    from src.config import supabase_config as supabase_mod

    saved = {m: dict(m.__dict__) for m in (config_mod, supabase_mod)}
    yield
    for mod, snapshot in saved.items():
        mod.__dict__.clear()
        mod.__dict__.update(snapshot)
    config_pkg.Config = config_mod.Config
    importlib.invalidate_caches()
