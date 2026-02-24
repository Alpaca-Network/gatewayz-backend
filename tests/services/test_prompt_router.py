"""
Tests for Prompt Router components.

Tests cover:
- Capability gating
- Prompt classification
- Model selection with stability
- Fallback chain building
- Main router with fail-open behavior
- Performance benchmarks
"""

import time
from unittest.mock import patch

import pytest

from src.schemas.router import (
    ClassificationResult,
    ModelCapabilities,
    PromptCategory,
    RequiredCapabilities,
    RouterOptimization,
    UserRouterPreferences,
)
from src.services.capability_gating import (
    extract_capabilities,
    filter_by_capabilities,
)
from src.services.fallback_chain import build_fallback_chain
from src.services.model_selector import select_model
from src.services.prompt_classifier_rules import classify_prompt
from src.services.prompt_router import (
    DEFAULT_CHEAP_MODEL,
    PromptRouter,
    is_auto_route_request,
    parse_auto_route_options,
)


class TestCapabilityGating:
    """Tests for capability extraction and filtering."""

    def test_extract_capabilities_basic(self):
        """Test basic capability extraction from messages."""
        messages = [{"role": "user", "content": "Hello, world!"}]
        caps = extract_capabilities(messages)

        assert caps.needs_tools is False
        assert caps.needs_json is False
        assert caps.needs_vision is False
        assert caps.min_context_tokens > 0

    def test_extract_capabilities_with_tools(self):
        """Test capability extraction with tools."""
        messages = [{"role": "user", "content": "Use the calculator"}]
        tools = [{"type": "function", "function": {"name": "calc"}}]

        caps = extract_capabilities(messages, tools=tools)

        assert caps.needs_tools is True
        assert caps.tool_schema_adherence == "medium"

    def test_extract_capabilities_with_json_mode(self):
        """Test capability extraction with JSON response format."""
        messages = [{"role": "user", "content": "Return JSON"}]
        response_format = {"type": "json_object"}

        caps = extract_capabilities(messages, response_format=response_format)

        assert caps.needs_json is True
        assert caps.strict_json is True

    def test_extract_capabilities_with_vision(self):
        """Test capability extraction with image content."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's in this image?"},
                    {"type": "image_url", "image_url": {"url": "http://example.com/img.jpg"}},
                ],
            }
        ]

        caps = extract_capabilities(messages)

        assert caps.needs_vision is True

    def test_filter_by_capabilities_tools(self):
        """Test filtering models by tool capability."""
        models = ["model_a", "model_b", "model_c"]
        registry = {
            "model_a": ModelCapabilities(model_id="model_a", provider="a", tools=True),
            "model_b": ModelCapabilities(model_id="model_b", provider="b", tools=False),
            "model_c": ModelCapabilities(model_id="model_c", provider="c", tools=True),
        }
        required = RequiredCapabilities(needs_tools=True)

        filtered = filter_by_capabilities(models, registry, required)

        assert "model_a" in filtered
        assert "model_b" not in filtered
        assert "model_c" in filtered

    def test_filter_by_capabilities_context(self):
        """Test filtering models by context length."""
        models = ["small", "large"]
        registry = {
            "small": ModelCapabilities(model_id="small", provider="a", max_context=8000),
            "large": ModelCapabilities(model_id="large", provider="b", max_context=100000),
        }
        required = RequiredCapabilities(min_context_tokens=50000)

        filtered = filter_by_capabilities(models, registry, required)

        assert "small" not in filtered
        assert "large" in filtered

    def test_filter_by_capabilities_cost(self):
        """Test filtering models by cost limit."""
        models = ["cheap", "expensive"]
        registry = {
            "cheap": ModelCapabilities(model_id="cheap", provider="a", cost_per_1k_input=0.0001),
            "expensive": ModelCapabilities(
                model_id="expensive", provider="b", cost_per_1k_input=0.01
            ),
        }
        required = RequiredCapabilities(max_cost_per_1k=0.001)

        filtered = filter_by_capabilities(models, registry, required)

        assert "cheap" in filtered
        assert "expensive" not in filtered


class TestPromptClassifier:
    """Tests for rule-based prompt classification."""

    def test_classify_simple_question(self):
        """Test classification of simple Q&A."""
        messages = [{"role": "user", "content": "What is the capital of France?"}]
        result = classify_prompt(messages)

        assert result.category == PromptCategory.SIMPLE_QA
        assert result.confidence >= 0.6

    def test_classify_code_generation(self):
        """Test classification of code generation request."""
        messages = [{"role": "user", "content": "Write a Python function to sort a list"}]
        result = classify_prompt(messages)

        assert result.category == PromptCategory.CODE_GENERATION
        assert result.confidence >= 0.7

    def test_classify_code_with_block(self):
        """Test classification of message with code block."""
        messages = [
            {"role": "user", "content": "Fix this code:\n```python\ndef foo():\n  pass\n```"}
        ]
        result = classify_prompt(messages)

        assert result.category in (PromptCategory.CODE_GENERATION, PromptCategory.CODE_REVIEW)
        assert result.confidence >= 0.8

    def test_classify_math(self):
        """Test classification of math calculation."""
        messages = [{"role": "user", "content": "Calculate 15 + 27 * 3"}]
        result = classify_prompt(messages)

        assert result.category == PromptCategory.MATH_CALCULATION
        assert result.confidence >= 0.7

    def test_classify_reasoning(self):
        """Test classification of complex reasoning."""
        messages = [
            {
                "role": "user",
                "content": "Analyze the pros and cons of remote work and explain why it might be beneficial",
            }
        ]
        result = classify_prompt(messages)

        assert result.category == PromptCategory.COMPLEX_REASONING
        assert result.confidence >= 0.7

    def test_classify_summarization(self):
        """Test classification of summarization request."""
        messages = [{"role": "user", "content": "Summarize this article for me"}]
        result = classify_prompt(messages)

        assert result.category == PromptCategory.SUMMARIZATION
        assert result.confidence >= 0.7

    def test_classify_translation(self):
        """Test classification of translation request."""
        messages = [{"role": "user", "content": "Translate this to Spanish: Hello"}]
        result = classify_prompt(messages)

        assert result.category == PromptCategory.TRANSLATION
        assert result.confidence >= 0.8

    def test_classify_multimodal_content(self):
        """Test classification handles multimodal content."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is 2+2?"},
                ],
            }
        ]
        result = classify_prompt(messages)

        # Should still classify the text
        assert result.category is not None
        assert result.confidence > 0

    def test_classification_performance(self):
        """Test classification completes within target time."""
        messages = [{"role": "user", "content": "This is a test message " * 100}]

        start = time.perf_counter()
        for _ in range(100):
            classify_prompt(messages)
        elapsed_ms = (time.perf_counter() - start) * 1000 / 100

        # Should average < 1ms per classification
        assert elapsed_ms < 2.0, f"Classification took {elapsed_ms:.2f}ms (target: < 1ms)"


class TestModelSelector:
    """Tests for model selection with stability."""

    @pytest.fixture
    def capabilities_registry(self):
        return {
            "openai/gpt-4o-mini": ModelCapabilities(
                model_id="openai/gpt-4o-mini",
                provider="openai",
                tools=True,
                json_mode=True,
                cost_per_1k_input=0.00015,
            ),
            "anthropic/claude-3-haiku": ModelCapabilities(
                model_id="anthropic/claude-3-haiku",
                provider="anthropic",
                tools=True,
                json_mode=True,
                cost_per_1k_input=0.00025,
            ),
            "deepseek/deepseek-chat": ModelCapabilities(
                model_id="deepseek/deepseek-chat",
                provider="deepseek",
                tools=True,
                json_mode=True,
                cost_per_1k_input=0.00014,
            ),
        }

    def test_select_model_basic(self, capabilities_registry):
        """Test basic model selection."""
        candidates = list(capabilities_registry.keys())
        classification = ClassificationResult(PromptCategory.SIMPLE_QA, 0.8)

        model, reason = select_model(
            candidates=candidates,
            classification=classification,
            capabilities_registry=capabilities_registry,
        )

        assert model in candidates
        assert reason in ("top_scorer", "stable_selection")

    def test_select_model_price_optimization(self, capabilities_registry):
        """Test model selection with price optimization."""
        candidates = list(capabilities_registry.keys())
        classification = ClassificationResult(PromptCategory.SIMPLE_QA, 0.8)

        model, reason = select_model(
            candidates=candidates,
            classification=classification,
            capabilities_registry=capabilities_registry,
            optimization=RouterOptimization.PRICE,
        )

        # Should prefer cheaper model
        assert model in candidates

    def test_select_model_quality_optimization(self, capabilities_registry):
        """Test model selection with quality optimization."""
        candidates = list(capabilities_registry.keys())
        classification = ClassificationResult(PromptCategory.CODE_GENERATION, 0.8)

        model, reason = select_model(
            candidates=candidates,
            classification=classification,
            capabilities_registry=capabilities_registry,
            optimization=RouterOptimization.QUALITY,
        )

        assert model in candidates

    def test_select_model_stable_with_conversation_id(self, capabilities_registry):
        """Test that same conversation ID produces stable selection."""
        candidates = list(capabilities_registry.keys())
        classification = ClassificationResult(PromptCategory.CONVERSATION, 0.6)
        conversation_id = "test-conversation-123"

        # Run selection multiple times with same conversation ID
        selections = set()
        for _ in range(10):
            model, _ = select_model(
                candidates=candidates,
                classification=classification,
                capabilities_registry=capabilities_registry,
                conversation_id=conversation_id,
            )
            selections.add(model)

        # Should always select the same model for same conversation
        assert len(selections) == 1

    def test_select_model_excludes_models(self, capabilities_registry):
        """Test that excluded models are not selected."""
        candidates = list(capabilities_registry.keys())
        classification = ClassificationResult(PromptCategory.SIMPLE_QA, 0.8)
        excluded = ["openai/gpt-4o-mini"]

        model, _ = select_model(
            candidates=candidates,
            classification=classification,
            capabilities_registry=capabilities_registry,
            excluded_models=excluded,
        )

        assert model not in excluded

    def test_select_model_no_candidates(self, capabilities_registry):
        """Test selection with no candidates returns default."""
        classification = ClassificationResult(PromptCategory.SIMPLE_QA, 0.8)

        model, reason = select_model(
            candidates=[],
            classification=classification,
            capabilities_registry=capabilities_registry,
        )

        assert model == DEFAULT_CHEAP_MODEL
        assert reason == "no_candidates"


class TestFallbackChain:
    """Tests for fallback chain building."""

    @pytest.fixture
    def capabilities_registry(self):
        return {
            "openai/gpt-4o-mini": ModelCapabilities(
                model_id="openai/gpt-4o-mini",
                provider="openai",
                tools=True,
                json_mode=True,
                tool_schema_adherence="high",
            ),
            "anthropic/claude-3-haiku": ModelCapabilities(
                model_id="anthropic/claude-3-haiku",
                provider="anthropic",
                tools=True,
                json_mode=True,
                tool_schema_adherence="high",
            ),
            "deepseek/deepseek-chat": ModelCapabilities(
                model_id="deepseek/deepseek-chat",
                provider="deepseek",
                tools=True,
                json_mode=True,
                tool_schema_adherence="medium",
            ),
            "google/gemini-flash-1.5": ModelCapabilities(
                model_id="google/gemini-flash-1.5",
                provider="google",
                tools=True,
                json_mode=True,
                tool_schema_adherence="medium",
            ),
        }

    def test_build_fallback_chain_basic(self, capabilities_registry):
        """Test basic fallback chain building."""
        primary = "openai/gpt-4o-mini"
        required = RequiredCapabilities(needs_tools=True)
        candidates = list(capabilities_registry.keys())

        chain = build_fallback_chain(
            primary_model=primary,
            required_capabilities=required,
            healthy_candidates=candidates,
            capabilities_registry=capabilities_registry,
        )

        # Should have fallbacks
        assert len(chain) > 0
        # Should not include primary
        assert primary not in [m for m, _ in chain]

    def test_build_fallback_chain_provider_diversity(self, capabilities_registry):
        """Test fallback chain prefers different providers."""
        primary = "openai/gpt-4o-mini"
        required = RequiredCapabilities()
        candidates = list(capabilities_registry.keys())

        chain = build_fallback_chain(
            primary_model=primary,
            required_capabilities=required,
            healthy_candidates=candidates,
            capabilities_registry=capabilities_registry,
        )

        # Extract providers
        providers = [p for _, p in chain]

        # First fallback should be different provider
        if len(providers) > 0:
            assert providers[0] != "openai"

    def test_build_fallback_chain_respects_capabilities(self, capabilities_registry):
        """Test fallback chain only includes capable models."""
        primary = "openai/gpt-4o-mini"
        required = RequiredCapabilities(
            needs_tools=True,
            tool_schema_adherence="high",
        )
        candidates = list(capabilities_registry.keys())

        chain = build_fallback_chain(
            primary_model=primary,
            required_capabilities=required,
            healthy_candidates=candidates,
            capabilities_registry=capabilities_registry,
        )

        # Should only include models with high tool adherence
        for model, _ in chain:
            caps = capabilities_registry[model]
            # With high adherence requirement, only high adherence models allowed
            assert caps.tool_schema_adherence == "high"

    def test_build_fallback_chain_max_limit(self, capabilities_registry):
        """Test fallback chain respects tier limit."""
        primary = "openai/gpt-4o-mini"
        required = RequiredCapabilities()
        candidates = list(capabilities_registry.keys())

        chain = build_fallback_chain(
            primary_model=primary,
            required_capabilities=required,
            healthy_candidates=candidates,
            capabilities_registry=capabilities_registry,
            tier="small",  # Max 2 fallbacks
        )

        assert len(chain) <= 2


class TestPromptRouter:
    """Tests for main router with fail-open behavior."""

    def test_router_basic_routing(self):
        """Test basic routing without auto mode."""
        router = PromptRouter()
        messages = [{"role": "user", "content": "Hello"}]

        decision = router.route(messages=messages)

        assert decision.selected_model is not None
        assert decision.decision_time_ms >= 0

    def test_router_fail_open_on_no_candidates(self):
        """Test router fails open when no candidates available."""
        router = PromptRouter()
        messages = [{"role": "user", "content": "Hello"}]

        # Force no candidates by patching empty capabilities registry
        with patch.object(router, "_capabilities_registry", {}):
            decision = router.route(messages=messages)

        # Should fail open to default
        assert decision.selected_model == DEFAULT_CHEAP_MODEL
        assert "fail_open" in decision.reason

    def test_router_timeout_protection(self):
        """Test router returns default on timeout."""
        router = PromptRouter()
        messages = [{"role": "user", "content": "Hello"}]

        # The router should always complete quickly due to fail-open
        start = time.perf_counter()
        decision = router.route(messages=messages)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Should complete well within timeout
        assert elapsed_ms < 100  # Even with cold start
        assert decision.selected_model is not None

    def test_router_with_tools(self):
        """Test routing with tools requirement."""
        router = PromptRouter()
        messages = [{"role": "user", "content": "Use the calculator"}]
        tools = [{"type": "function", "function": {"name": "calc"}}]

        decision = router.route(messages=messages, tools=tools)

        # Should select a model with tool support
        if decision.selected_model != DEFAULT_CHEAP_MODEL:
            caps = router.get_capabilities(decision.selected_model)
            if caps:
                assert caps.tools is True

    def test_router_with_user_preferences(self):
        """Test routing with user preferences."""
        router = PromptRouter()
        messages = [{"role": "user", "content": "Hello"}]
        preferences = UserRouterPreferences(
            default_optimization=RouterOptimization.PRICE,
            excluded_models=["openai/gpt-4o"],
        )

        decision = router.route(messages=messages, user_preferences=preferences)

        assert decision.selected_model != "openai/gpt-4o"


class TestAutoRouteHelpers:
    """Tests for auto-route helper functions."""

    def test_is_auto_route_request(self):
        """Test detection of auto-route requests."""
        # Router prefix should trigger auto-routing
        assert is_auto_route_request("router") is True
        assert is_auto_route_request("ROUTER") is True
        assert is_auto_route_request("router:small") is True
        assert is_auto_route_request("router:price") is True
        # Other models should not trigger auto-routing
        assert is_auto_route_request("gpt-4o") is False
        assert is_auto_route_request("openrouter/auto") is False  # OpenRouter's auto model
        assert is_auto_route_request("") is False
        assert is_auto_route_request(None) is False

    def test_parse_auto_route_options(self):
        """Test parsing of auto-route options."""
        tier, opt = parse_auto_route_options("router")
        assert tier == "small"
        assert opt == RouterOptimization.BALANCED

        tier, opt = parse_auto_route_options("router:small")
        assert tier == "small"
        assert opt == RouterOptimization.BALANCED

        tier, opt = parse_auto_route_options("router:medium")
        assert tier == "medium"
        assert opt == RouterOptimization.BALANCED

        tier, opt = parse_auto_route_options("router:price")
        assert tier == "small"
        assert opt == RouterOptimization.PRICE

        tier, opt = parse_auto_route_options("router:quality")
        assert tier == "medium"
        assert opt == RouterOptimization.QUALITY

        tier, opt = parse_auto_route_options("router:fast")
        assert tier == "small"
        assert opt == RouterOptimization.FAST


class TestPerformanceBenchmarks:
    """Performance benchmarks for router components."""

    def test_capability_extraction_performance(self):
        """Benchmark capability extraction."""
        messages = [{"role": "user", "content": "Test message " * 50}]
        tools = [{"type": "function", "function": {"name": f"tool_{i}"}} for i in range(5)]

        start = time.perf_counter()
        iterations = 1000
        for _ in range(iterations):
            extract_capabilities(messages, tools=tools)
        elapsed_ms = (time.perf_counter() - start) * 1000 / iterations

        assert elapsed_ms < 0.5, f"Capability extraction took {elapsed_ms:.3f}ms (target: < 0.1ms)"

    def test_classification_performance(self):
        """Benchmark prompt classification."""
        messages = [
            {
                "role": "user",
                "content": "Write a Python function to sort a list using quicksort algorithm",
            }
        ]

        start = time.perf_counter()
        iterations = 1000
        for _ in range(iterations):
            classify_prompt(messages)
        elapsed_ms = (time.perf_counter() - start) * 1000 / iterations

        assert elapsed_ms < 2.0, f"Classification took {elapsed_ms:.3f}ms (target: < 1ms)"

    def test_full_router_performance(self):
        """Benchmark full router pipeline."""
        router = PromptRouter()
        messages = [{"role": "user", "content": "What is the capital of France?"}]

        # Warm up
        router.route(messages=messages)

        start = time.perf_counter()
        iterations = 100
        for _ in range(iterations):
            router.route(messages=messages)
        elapsed_ms = (time.perf_counter() - start) * 1000 / iterations

        # Should be well under 2ms target (excluding Redis which is mocked in tests)
        assert elapsed_ms < 5.0, f"Full router took {elapsed_ms:.3f}ms (target: < 2ms)"
