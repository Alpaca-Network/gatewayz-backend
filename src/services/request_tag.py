"""Caller-supplied attribution tag, carried from the request onto the usage record.

FlashyOS's compute-attribution plan (Board Series 02) names this the single
highest-leverage thing this gateway can ship: an agent says which initiative a
call belongs to AT THE CALL, and the gateway records it. Attribution decided at
the boundary survives a caller that drops the response, crashes, or never reads
it back -- reconstructing it afterwards from timestamps does not.

Deliberately unopinionated about what a tag MEANS. FlashyOS sends initiative
ids; someone else will send a customer, a job, an experiment. The gateway's job
is to carry the string faithfully and refuse the ones that would hurt it.
"""

from __future__ import annotations

import re

# The header a caller sets. `x-tag` is what the partner plan calls it; the
# vendor-prefixed form is the one we document, and both are accepted so a
# caller following either reads correctly.
TAG_HEADERS = ("x-gatewayz-tag", "x-tag")

MAX_TAG_LENGTH = 128

# Identifier-ish only. This string is persisted, aggregated, and later rendered
# in dashboards on both sides, so it must not be able to carry markup, control
# characters, or a newline that would break a log line it appears in.
_ALLOWED = re.compile(r"^[A-Za-z0-9._:/@=-]+$")


def extract_request_tag(request) -> str | None:
    """The tag on this request, or None.

    Never raises and never rejects the REQUEST: an unusable tag is dropped and
    the call proceeds unattributed. Attribution is opt-in, so a malformed tag
    must not cost a caller their inference -- that would make instrumenting a
    fleet riskier than not instrumenting it.
    """
    if request is None:
        return None
    try:
        headers = request.headers
    except Exception:  # noqa: BLE001 - a request object without headers is not fatal
        return None

    for name in TAG_HEADERS:
        raw = headers.get(name)
        if not raw:
            continue
        tag = raw.strip()
        if not tag or len(tag) > MAX_TAG_LENGTH or not _ALLOWED.match(tag):
            return None
        return tag
    return None
