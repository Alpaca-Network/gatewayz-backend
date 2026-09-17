"""Streaming errors are classified on a labelled status, never on loose digits.

`stream_generator`'s except block picked a user-facing message by scanning the raw error
for "429", "401", "503", "502", "404". Provider errors carry a 64-character hex API key
id, and a random hex id contains any given digit triple about one time in sixty-five, so
each of those patterns fired on a credential roughly 1.5% of the time -- and a false match
won whenever the true branch sat later in the chain.

The consequence was not cosmetic. `error_type` is what the SSE payload reports, what is
persisted to chat_completion_requests.error_message, and what gates the auto-refund arm.
Telling somebody "Authentication failed. Please check your API key" because their timeout
happened to contain the digits 401 sends them to debug a credential that is fine.

The budget branch was reordered for this same reason in #2344. These tests pin the
general fix: classify on parse_upstream_status(), fall back to alphabetic phrases, never
to a digit scan.
"""

from __future__ import annotations

import pytest

from src.utils.errors import parse_upstream_status

# A real OpenRouter key id that happens to contain "429", "401" and "402".
KEY_ID_WITH_429 = "f001429593544cd92610592c96fee5e341f53e759e3f07aa5089c82159c5ed03"
# Constructed ids carrying the other three digit triples, to cover each branch.
KEY_ID_WITH_401 = "ab401cdef0123456789abcdef0123456789abcdef0123456789abcdef0123456"
KEY_ID_WITH_503 = "ab503cdef0123456789abcdef0123456789abcdef0123456789abcdef0123456"
KEY_ID_WITH_404 = "ab404cdef0123456789abcdef0123456789abcdef0123456789abcdef0123456"


class _ExplodingStream:
    """A provider stream whose first chunk raises."""

    def __init__(self, raw_error):
        self._raw_error = raw_error

    def __iter__(self):
        return self

    def __next__(self):
        raise Exception(self._raw_error)


async def _classify(raw_error):
    """Drive stream_generator's except block and return (error_type, body)."""
    from src.routes.chat_streaming import stream_generator

    chunks = []
    async for chunk in stream_generator(
        stream=_ExplodingStream(raw_error),
        user=None,
        api_key=None,
        model="claude-sonnet-5",
        trial={},
        environment_tag=None,
        session_id=None,
        messages=[{"role": "user", "content": "x"}],
        provider="anthropic",
        is_anonymous=True,
        request_id=None,  # skip the failed-request DB write; not what this tests
    ):
        chunks.append(chunk)
    body = "".join(str(c) for c in chunks)
    for name in (
        "capacity_error",
        "rate_limit_error",
        "auth_error",
        "provider_error",
        "timeout_error",
        "not_found_error",
        "stream_error",
    ):
        if f'"type": "{name}"' in body or f'"{name}"' in body:
            return name, body
    return None, body


# --------------------------------------------------------------------------------------
# parse_upstream_status: the labelled shapes we accept, and what must never match
# --------------------------------------------------------------------------------------


class TestParseUpstreamStatus:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Error code: 429 - {'error': ...}", 429),  # OpenAI / Anthropic SDKs
            ("error_code=503", 503),
            ("status 502", 502),
            ("status_code: 404", 404),
            ('{"code": 401, "message": "bad key"}', 401),
            ("{'code': 402}", 402),
            ("HTTP 404", 404),
            ("ERROR CODE: 401", 401),  # case-insensitive
        ],
    )
    def test_labelled_statuses_are_read(self, raw, expected):
        assert parse_upstream_status(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            f"Timed out after 30s (key {KEY_ID_WITH_429})",
            f"Timed out after 30s (key {KEY_ID_WITH_401})",
            f"https://openrouter.ai/workspaces/default/keys/{KEY_ID_WITH_429}",
            "unicode 404 glyph missing",  # "code" inside a word is not a label
            "Error code: 4029",  # four digits is not a status
            "connection reset by peer",
        ],
    )
    def test_unlabelled_digits_are_never_read_as_a_status(self, raw):
        assert parse_upstream_status(raw) is None

    def test_every_label_alternative_is_alphabetic(self):
        # This is the property that makes the fallback safe: hex ids are [0-9a-f], and
        # every label below contains a letter outside that set, so no key id can supply
        # one. If a future label were added using only hex letters ("dec", "cafe"), the
        # bug would come back through the new label.
        import re as _re

        from src.utils.errors import _UPSTREAM_STATUS_RE

        labels = _re.findall(r"[a-z]{3,}", _UPSTREAM_STATUS_RE.pattern)
        assert labels, "expected literal label words in the pattern"
        for label in labels:
            if label in ("ignorecase", "s"):
                continue
            assert set(label) - set("0123456789abcdef"), f"{label!r} is hex-representable"


# --------------------------------------------------------------------------------------
# The bug, per branch
# --------------------------------------------------------------------------------------


class TestKeyIdDigitsDoNotClassify:
    async def test_a_key_id_containing_401_is_not_an_auth_error(self):
        # THE bug the ticket is about. Before this fix the user was told their API key
        # was wrong because a credential happened to contain three digits.
        assert "401" in KEY_ID_WITH_401, "the fixture must actually contain the digits"

        error_type, body = await _classify(f"Request timed out after 30s (key {KEY_ID_WITH_401})")

        assert error_type != "auth_error"
        assert "Authentication failed" not in body
        assert "check your API key" not in body
        # ...and the true classification still wins: the word "timed out" is present.
        assert error_type == "timeout_error"

    async def test_a_key_id_containing_429_is_not_a_rate_limit(self):
        assert "429" in KEY_ID_WITH_429
        error_type, body = await _classify(f"Request timed out after 30s (key {KEY_ID_WITH_429})")
        assert error_type != "rate_limit_error"
        assert "Rate limit exceeded" not in body
        assert error_type == "timeout_error"

    async def test_a_key_id_containing_503_is_not_a_provider_outage(self):
        assert "503" in KEY_ID_WITH_503
        error_type, _ = await _classify(f"Request timed out after 30s (key {KEY_ID_WITH_503})")
        assert error_type != "provider_error"
        assert error_type == "timeout_error"

    async def test_a_key_id_containing_404_is_not_a_missing_model(self):
        # No timeout phrase here, unlike the tests above: the not_found arm is checked
        # *after* the timeout arm, so a "timed out" fixture would be classified before
        # ever reaching the branch under test and the assertion would pass whether or not
        # the bug were present. Mutation testing caught exactly that.
        assert "404" in KEY_ID_WITH_404
        error_type, body = await _classify(f"unexpected failure (key {KEY_ID_WITH_404})")
        assert error_type != "not_found_error"
        assert "was not found" not in body
        assert error_type == "stream_error"

    async def test_a_key_id_alone_falls_to_the_generic_arm(self):
        # No phrase, no labelled status: the honest answer is the generic one, not a
        # confident guess pulled out of a credential.
        # Deliberately contains none of the phrase signals -- "upstream" and "provider"
        # are real words the classifier still keys off, and using one here would test the
        # fixture rather than the fallback.
        error_type, body = await _classify(f"unexpected failure key={KEY_ID_WITH_401}")
        assert error_type == "stream_error"
        assert "Authentication failed" not in body
        # The sanitizer still strips the key id from what the user sees.
        assert KEY_ID_WITH_401 not in body


# --------------------------------------------------------------------------------------
# Correct classifications must survive the fix
# --------------------------------------------------------------------------------------


class TestGenuineErrorsStillClassify:
    @pytest.mark.parametrize(
        ("raw", "expected_type", "expected_copy"),
        [
            # via labelled status
            ("Error code: 429 - too many requests upstream", "rate_limit_error", "Rate limit"),
            ("Error code: 401 - invalid key", "auth_error", "Authentication failed"),
            ("Error code: 503 - upstream down", "provider_error", "temporarily unavailable"),
            ("Error code: 502 - bad gateway", "provider_error", "temporarily unavailable"),
            ("Error code: 404 - no such model", "not_found_error", "was not found"),
            # via phrase, with no status labelled anywhere
            ("Rate limit exceeded, slow down", "rate_limit_error", "Rate limit"),
            ("unauthorized: bad credentials", "auth_error", "Authentication failed"),
            (
                "Client error '401 Unauthorized' for url",
                "auth_error",
                "Authentication failed",
            ),
            ("upstream connection failed", "provider_error", "temporarily unavailable"),
            ("Request timed out", "timeout_error", "timed out"),
            ("model not found", "not_found_error", "was not found"),
        ],
    )
    async def test_real_errors_keep_their_message(self, raw, expected_type, expected_copy):
        error_type, body = await _classify(raw)
        assert error_type == expected_type
        assert expected_copy in body

    async def test_budget_exhaustion_still_wins_over_everything(self):
        # #2344's branch order is unchanged by this fix.
        from src.utils.errors import PROVIDER_CAPACITY_MESSAGE

        error_type, body = await _classify(
            "Your credit balance is too low to access the Anthropic API"
        )
        assert error_type == "capacity_error"
        assert PROVIDER_CAPACITY_MESSAGE in body
