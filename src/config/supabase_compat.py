"""Let supabase-py 2.12 accept Supabase's new API keys (sb_secret_ / sb_publishable_).

supabase==2.12.0 refuses any key that is not shaped like a JWT
(`supabase/_sync/client.py`: "Invalid API key"). Supabase's new secret keys are
opaque strings, so with the legacy JWT keys disabled the backend could not
build a client at all and every authenticated request returned 503
(2026-11-27, after the leaked legacy service_role key was disabled).

The rest of the client already works with the new keys: it sends the key as
both `apikey` and `Authorization: Bearer`, which the Supabase API gateway
accepts for sb_secret_ keys (verified against production). Only the format
check is in the way, so this module relaxes exactly that check for keys with
the new prefixes and leaves every other `re` call in those modules untouched.

Remove this module once supabase-py is upgraded to a release that accepts the
new keys natively.
"""

from __future__ import annotations

import re as _re
from types import ModuleType

NEW_KEY_PREFIXES = ("sb_secret_", "sb_publishable_")
_NEW_KEY_RE = _re.compile(r"^sb_(secret|publishable)_[A-Za-z0-9_-]+$")
# The exact pattern supabase-py 2.12 uses for its JWT check.
_JWT_CHECK = r"^[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*$"


class _ReShim(ModuleType):
    """`re` for supabase's client modules: identical except for the key check."""

    def __init__(self) -> None:
        super().__init__("re")

    def __getattr__(self, name):
        return getattr(_re, name)

    @staticmethod
    def match(pattern, string, flags=0):
        if pattern == _JWT_CHECK and isinstance(string, str) and _NEW_KEY_RE.match(string):
            return _NEW_KEY_RE.match(string)
        return _re.match(pattern, string, flags)


def allow_new_api_keys() -> None:
    """Idempotent. Safe to call before any client is created."""
    import supabase._async.client as async_client
    import supabase._sync.client as sync_client

    for module in (sync_client, async_client):
        if not isinstance(getattr(module, "re", None), _ReShim):
            module.re = _ReShim()
