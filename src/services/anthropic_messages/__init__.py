"""Helpers for the Anthropic Messages API route (``POST /v1/messages``).

- ``errors``: gateway errors in Anthropic's error envelope, with internal
  details removed.
- ``stream``: the pipeline's OpenAI-shaped stream re-emitted as Anthropic
  events, or Anthropic's own events relayed for passed-through Claude requests.
- ``tokens``: the input-token estimate behind ``/v1/messages/count_tokens``.

The route itself (``src/routes/messages.py``) only wires these together.
"""
