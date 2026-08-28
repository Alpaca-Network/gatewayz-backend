"""Guard: the pinned openai SDK must accept the params the gateway forwards.

Issue #2236: openai==1.44.0 predates ``max_completion_tokens`` (SDK 1.45.0) and
``reasoning_effort``, so every gpt-5-family request died with
``Completions.create() got an unexpected keyword argument 'max_completion_tokens'``.
The gateway renames ``max_tokens`` to ``max_completion_tokens`` for OpenAI
reasoning models (see src/services/providers/reasoning_effort.py) and forwards
``reasoning_effort`` — the installed SDK must know both kwargs or the rename
turns a valid request into a TypeError.
"""

import inspect

from openai.resources.chat.completions import Completions


def _create_params():
    return inspect.signature(Completions.create).parameters


def test_sdk_accepts_max_completion_tokens():
    assert "max_completion_tokens" in _create_params(), (
        "installed openai SDK predates max_completion_tokens; "
        "gpt-5/o-series requests will TypeError (issue #2236)"
    )


def test_sdk_accepts_reasoning_effort():
    assert "reasoning_effort" in _create_params(), (
        "installed openai SDK predates reasoning_effort; " "reasoning requests will TypeError"
    )
