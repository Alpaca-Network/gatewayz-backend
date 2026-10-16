"""Unit tests for src/services/key_purpose.py -- the validator-mode switch.

Every guard in the request path keys off these predicates, so each is pinned
here with both a validator and a general case: a general key must come out
False everywhere (byte-identical behaviour), a validator key True.
"""

import asyncio

import pytest
from fastapi import HTTPException

from src.services import key_purpose as kp


def _run(coro):
    """Run a coroutine on a private loop.

    Not asyncio.run(): that unsets the thread's current event loop on exit,
    which breaks later tests in the same worker that call get_event_loop().
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


VALIDATOR = {"id": 1, kp.USER_FIELD: "validator"}
GENERAL = {"id": 2, kp.USER_FIELD: None}
LEGACY = {"id": 3}  # legacy users.api_key path: no key_purpose at all


class TestNormalizePurpose:
    @pytest.mark.parametrize("value", [None, "", "general", "GENERAL", " general "])
    def test_general_is_stored_as_null(self, value):
        assert kp.normalize_purpose(value) is None

    @pytest.mark.parametrize("value", ["validator", "Validator", " VALIDATOR "])
    def test_validator(self, value):
        assert kp.normalize_purpose(value) == "validator"

    @pytest.mark.parametrize("value", ["validators", "nolog", "private", "admin"])
    def test_anything_else_is_rejected_not_stored(self, value):
        with pytest.raises(ValueError, match="Invalid purpose"):
            kp.normalize_purpose(value)


class TestIsValidatorKey:
    def test_validator(self):
        assert kp.is_validator_key(VALIDATOR) is True

    @pytest.mark.parametrize("user", [GENERAL, LEGACY, None, {}])
    def test_everything_else_is_general(self, user):
        assert kp.is_validator_key(user) is False

    def test_reads_the_key_field_not_a_users_column(self):
        # A users-table column called "purpose" must not switch the mode on.
        assert kp.is_validator_key({"id": 4, "purpose": "validator"}) is False


class TestRequestContext:
    def test_default_is_off(self):
        async def probe():
            return kp.validator_mode_active()

        assert _run(probe()) is False

    def test_bound_value_is_seen_by_to_thread_and_tasks(self):
        async def request():
            kp.bind_request_key_purpose(VALIDATOR)
            in_thread = await asyncio.to_thread(kp.validator_mode_active)
            in_task = await asyncio.create_task(asyncio.sleep(0, kp.validator_mode_active()))
            return in_thread, in_task

        assert _run(request()) == (True, True)

    def test_does_not_leak_into_a_concurrent_request(self):
        async def validator_request(started, release):
            kp.bind_request_key_purpose(VALIDATOR)
            started.set()
            await release.wait()
            return kp.validator_mode_active()

        async def general_request(started, release):
            await started.wait()
            seen = kp.validator_mode_active()
            release.set()
            return seen

        async def main():
            started, release = asyncio.Event(), asyncio.Event()
            return await asyncio.gather(
                asyncio.create_task(validator_request(started, release)),
                asyncio.create_task(general_request(started, release)),
            )

        assert _run(main()) == [True, False]

    def test_suppress_request_logging_accepts_either_signal(self):
        async def run():
            out = [kp.suppress_request_logging(VALIDATOR), kp.suppress_request_logging(GENERAL)]
            kp.bind_request_key_purpose(VALIDATOR)
            out.append(kp.suppress_request_logging())
            kp.bind_request_key_purpose(GENERAL)
            out.append(kp.suppress_request_logging())
            return out

        assert _run(run()) == [True, False, True, False]


class TestRoutingAlias:
    @pytest.mark.parametrize(
        "model",
        [
            "auto",
            "AUTO",
            "openrouter/auto",
            "gatewayz/auto",
            "gatewayz-router",
            "router:code",
            "router",
            "auto:fast",
            "gatewayz-general",
            "gatewayz-code-v2",
        ],
    )
    def test_aliases(self, model):
        assert kp.is_routing_alias(model) is True

    @pytest.mark.parametrize(
        "model", ["openai/gpt-4o", "anthropic/claude-fable-5", "z-ai/glm-4.6", None, ""]
    )
    def test_real_models(self, model):
        assert kp.is_routing_alias(model) is False

    def test_validator_alias_is_refused_with_a_typed_400(self):
        with pytest.raises(HTTPException) as exc:
            kp.enforce_no_routing_alias(VALIDATOR, "openrouter/auto")
        assert exc.value.status_code == 400
        assert exc.value.detail["error"]["code"] == "model_substitution_refused"

    def test_general_key_alias_is_left_to_existing_routing(self):
        kp.enforce_no_routing_alias(GENERAL, "openrouter/auto")  # no raise
        kp.enforce_no_routing_alias(None, "auto")  # anonymous: no raise


class TestSameModel:
    @pytest.mark.parametrize(
        "requested,sent",
        [
            ("openai/gpt-4o", "gpt-4o"),
            ("openai/gpt-4o", "openai/gpt-4o"),
            ("OpenAI/GPT-4o", "gpt-4o"),
            ("anthropic/claude-fable-5", "claude-fable-5"),
            ("deepseek-ai/deepseek-v3", "accounts/fireworks/models/deepseek-v3"),
            ("xiaomi/mimo-v2-flash:free", "xiaomi/mimo-v2-flash"),
        ],
    )
    def test_same(self, requested, sent):
        assert kp.is_same_model(requested, sent) is True

    @pytest.mark.parametrize(
        "requested,sent",
        [
            # Real remaps found in src/services/model_transformations.py.
            ("google/gemini-1.5-pro", "gemini-2.5-flash"),
            ("deepseek-ai/deepseek-v3", "accounts/fireworks/models/deepseek-v3p1"),
            ("openrouter/auto", "llama-3.3-70b"),
            ("openai/gpt-4o-mini", "gpt-4o"),
            ("openai/gpt-4o", None),
            (None, "gpt-4o"),
        ],
    )
    def test_different(self, requested, sent):
        assert kp.is_same_model(requested, sent) is False

    def test_validator_substitution_refused(self):
        with pytest.raises(HTTPException) as exc:
            kp.enforce_same_model(VALIDATOR, "google/gemini-1.5-pro", "gemini-2.5-flash")
        assert exc.value.status_code == 400
        assert exc.value.detail["error"]["code"] == "model_substitution_refused"

    def test_general_key_keeps_existing_mapping_behaviour(self):
        kp.enforce_same_model(GENERAL, "google/gemini-1.5-pro", "gemini-2.5-flash")  # no raise
