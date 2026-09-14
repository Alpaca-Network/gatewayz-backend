"""A caller's attribution tag is carried onto the usage record, or dropped safely.

FlashyOS's compute-attribution plan (Board Series 02) calls request-tag
passthrough "the single highest-leverage thing they can ship": attribution
decided AT THE CALL survives a caller that drops the response, and cannot be
reconstructed afterwards from timestamps.

Two properties matter more than the happy path:

  1. attribution is OPT-IN -- an untagged call behaves exactly as before, and a
     malformed tag costs the caller a tag, never their inference. Instrumenting
     a fleet must not be riskier than leaving it uninstrumented.
  2. the tag is persisted, aggregated and later RENDERED on dashboards on both
     sides, so it must not be able to carry markup, a control character, or a
     newline that breaks the log line it lands in.
"""

from __future__ import annotations

import pytest

from src.services.request_tag import MAX_TAG_LENGTH, extract_request_tag


class _Req:
    def __init__(self, headers):
        self.headers = headers


def test_reads_the_documented_header():
    assert extract_request_tag(_Req({"x-gatewayz-tag": "init/orbital-refi-q3"})) == (
        "init/orbital-refi-q3"
    )


def test_reads_the_partner_spelling_too():
    # The partner plan calls it `x-tag`; ours is vendor-prefixed. A caller
    # following either document has to read correctly.
    assert extract_request_tag(_Req({"x-tag": "init/abc"})) == "init/abc"


def test_untagged_request_is_none_not_an_error():
    assert extract_request_tag(_Req({})) is None


def test_no_request_object_is_none():
    # Anonymous and internal paths can construct a handler without a Request.
    assert extract_request_tag(None) is None


def test_whitespace_is_trimmed():
    assert extract_request_tag(_Req({"x-tag": "  init/abc  "})) == "init/abc"


@pytest.mark.parametrize(
    "bad",
    [
        "a" * (MAX_TAG_LENGTH + 1),  # unbounded strings land in JSONB and dashboards
        "init/a b",  # whitespace inside
        "init\nabc",  # would break a log line
        "init\x00abc",  # control character
        "<script>alert(1)</script>",  # rendered on two dashboards
        "init/'; drop table--",
        "",
    ],
)
def test_unusable_tags_are_dropped_not_raised(bad):
    # Dropped, so the CALL still succeeds untagged. Rejecting the request would
    # make a typo in instrumentation cost real inference.
    assert extract_request_tag(_Req({"x-tag": bad})) is None


def test_a_tag_at_the_limit_is_accepted():
    tag = "a" * MAX_TAG_LENGTH
    assert extract_request_tag(_Req({"x-tag": tag})) == tag


def test_headers_that_raise_do_not_propagate():
    class _Broken:
        @property
        def headers(self):
            raise RuntimeError("no headers here")

    assert extract_request_tag(_Broken()) is None
