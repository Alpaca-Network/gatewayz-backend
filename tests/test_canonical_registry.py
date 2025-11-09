"""
Tests for the Canonical Model Registry and Multi-Provider Routing
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

from src.services.canonical_model_registry import (
    CanonicalModel,
    CanonicalModelRegistry,
    ModelHealthMetrics,
    get_canonical_registry,
)
from src.services.multi_provider_registry import ProviderConfig


class TestCanonicalModel:
    """Test the CanonicalModel class"""

    def test_create_canonical_model(self):
        """Test creating a canonical model"""
        model = CanonicalModel(
            id="gpt-4o",
            name="GPT-4o",
            description="OpenAI's flagship model",
        )

        assert model.id == "gpt-4o"
        assert model.name == "GPT-4o"
        assert model.description == "OpenAI's flagship model"
        assert len(model.providers) == 0
        assert len(model.modalities) == 0

    def test_add_provider(self):
        """Test adding providers to a canonical model"""
        model = CanonicalModel(id="llama-70b", name="Llama 70B")

        # Add first provider
        model.add_provider(
            provider_name="openrouter",
            provider_model_id="meta-llama/llama-70b",
            context_length=4096,
            modalities=["text"],
            features=["streaming", "function_calling"],
            input_cost=0.5,
            output_cost=0.5,
        )

        assert "openrouter" in model.providers
        assert model.provider_model_ids["openrouter"] == "meta-llama/llama-70b"
        assert model.context_lengths["openrouter"] == 4096
        assert "text" in model.modalities
        assert "streaming" in model.features
        assert model.min_input_cost == 0.5
        assert model.max_input_cost == 0.5

        # Add second provider with different costs
        model.add_provider(
            provider_name="together",
            provider_model_id="meta-llama/Llama-70B-Instruct",
            context_length=8192,
            modalities=["text", "code"],
            features=["streaming"],
            input_cost=0.3,
            output_cost=0.3,
        )

        assert "together" in model.providers
        assert len(model.providers) == 2
        assert "code" in model.modalities
        assert model.min_input_cost == 0.3  # Updated to lower cost
        assert model.max_input_cost == 0.5  # Still the higher cost
        assert model.context_lengths["together"] == 8192

    def test_get_cheapest_provider(self):
        """Test getting the cheapest provider"""
        model = CanonicalModel(id="test-model", name="Test Model")

        model.add_provider(
            provider_name="expensive",
            provider_model_id="test-expensive",
            input_cost=1.0,
            output_cost=2.0,
        )

        model.add_provider(
            provider_name="cheap",
            provider_model_id="test-cheap",
            input_cost=0.1,
            output_cost=0.1,
        )

        model.add_provider(
            provider_name="free",
            provider_model_id="test-free",
            input_cost=0.0,
            output_cost=0.0,
        )

        assert model.get_cheapest_provider() == "free"

    def test_to_multi_provider_model(self):
        """Test converting to MultiProviderModel"""
        model = CanonicalModel(
            id="claude-3.5-sonnet",
            name="Claude 3.5 Sonnet",
            description="Anthropic's latest model",
        )

        model.add_provider(
            provider_name="openrouter",
            provider_model_id="anthropic/claude-3.5-sonnet",
            context_length=200000,
            modalities=["text", "image"],
            features=["streaming"],
            input_cost=3.0,
            output_cost=15.0,
        )

        multi_provider = model.to_multi_provider_model()

        assert multi_provider.id == "claude-3.5-sonnet"
        assert multi_provider.name == "Claude 3.5 Sonnet"
        assert len(multi_provider.providers) == 1
        assert multi_provider.context_length == 200000
        assert "text" in multi_provider.modalities


class TestModelHealthMetrics:
    """Test the ModelHealthMetrics class"""

    def test_health_metrics_initialization(self):
        """Test health metrics initialization"""
        metrics = ModelHealthMetrics(
            provider="openrouter",
            model_id="gpt-4",
        )

        assert metrics.provider == "openrouter"
        assert metrics.model_id == "gpt-4"
        assert metrics.success_count == 0
        assert metrics.failure_count == 0
        assert metrics.success_rate == 0.0
        assert metrics.is_healthy is True
        assert metrics.circuit_breaker_state == "closed"

    def test_record_success(self):
        """Test recording successful requests"""
        metrics = ModelHealthMetrics(
            provider="together",
            model_id="llama-70b",
        )

        # Record successes
        metrics.record_success(100.0)
        metrics.record_success(200.0)

        assert metrics.success_count == 2
        assert metrics.failure_count == 0
        assert metrics.success_rate == 1.0
        assert metrics.avg_latency_ms == 150.0

    def test_circuit_breaker(self):
        """Test circuit breaker functionality"""
        metrics = ModelHealthMetrics(
            provider="fireworks",
            model_id="mixtral",
            failure_threshold=3,
        )

        # Record failures up to threshold
        metrics.record_failure()
        assert metrics.circuit_breaker_state == "closed"

        metrics.record_failure()
        assert metrics.circuit_breaker_state == "closed"

        # Third failure should open circuit
        metrics.record_failure()
        assert metrics.circuit_breaker_state == "open"
        assert not metrics.is_healthy

    def test_circuit_breaker_recovery(self):
        """Test circuit breaker recovery after timeout"""
        metrics = ModelHealthMetrics(
            provider="deepinfra",
            model_id="llama",
            failure_threshold=2,
            recovery_timeout=timedelta(seconds=0),  # Immediate recovery for test
        )

        # Open circuit
        metrics.record_failure()
        metrics.record_failure()
        assert metrics.circuit_breaker_state == "open"

        # Record success after timeout - should move to half-open
        metrics.record_success()
        assert metrics.circuit_breaker_state == "half-open"

        # Another success should close circuit
        metrics.record_success()
        assert metrics.circuit_breaker_state == "closed"
        assert metrics.is_healthy


class TestCanonicalModelRegistry:
    """Test the CanonicalModelRegistry class"""

    def test_register_canonical_model(self):
        """Test registering a canonical model"""
        registry = CanonicalModelRegistry()

        model = CanonicalModel(
            id="test-model",
            name="Test Model",
        )
        model.add_provider(
            provider_name="provider1",
            provider_model_id="test-id",
            input_cost=1.0,
            output_cost=1.0,
        )

        registry.register_canonical_model(model)

        # Check model is registered
        retrieved = registry.get_canonical_model("test-model")
        assert retrieved is not None
        assert retrieved.id == "test-model"

    def test_model_aliases(self):
        """Test model alias resolution"""
        registry = CanonicalModelRegistry()

        model = CanonicalModel(id="gpt-4o", name="GPT-4o")
        registry.register_canonical_model(model)

        # Add aliases
        registry.add_alias("gpt4o", "gpt-4o")
        registry.add_alias("gpt-4-o", "gpt-4o")

        # Test alias resolution
        assert registry.resolve_model_id("gpt4o") == "gpt-4o"
        assert registry.resolve_model_id("gpt-4-o") == "gpt-4o"
        assert registry.resolve_model_id("gpt-4o") == "gpt-4o"
        assert registry.resolve_model_id("unknown") == "unknown"

        # Test getting model by alias
        model_by_alias = registry.get_canonical_model("gpt4o")
        assert model_by_alias is not None
        assert model_by_alias.id == "gpt-4o"

    def test_select_providers_with_failover(self):
        """Test provider selection with different strategies"""
        registry = CanonicalModelRegistry()

        model = CanonicalModel(id="llama-70b", name="Llama 70B")

        # Add providers with different costs and priorities
        model.add_provider(
            provider_name="expensive",
            provider_model_id="llama-expensive",
            input_cost=1.0,
            output_cost=1.0,
        )
        model.providers["expensive"].priority = 3

        model.add_provider(
            provider_name="cheap",
            provider_model_id="llama-cheap",
            input_cost=0.1,
            output_cost=0.1,
        )
        model.providers["cheap"].priority = 2

        model.add_provider(
            provider_name="priority",
            provider_model_id="llama-priority",
            input_cost=0.5,
            output_cost=0.5,
        )
        model.providers["priority"].priority = 1

        registry.register_canonical_model(model)

        # Test priority strategy (default)
        providers = registry.select_providers_with_failover(
            model_id="llama-70b",
            max_providers=3,
            selection_strategy="priority",
        )
        assert len(providers) == 3
        assert providers[0][0] == "priority"  # Lowest priority number = highest priority
        assert providers[1][0] == "cheap"
        assert providers[2][0] == "expensive"

        # Test cost strategy
        providers = registry.select_providers_with_failover(
            model_id="llama-70b",
            max_providers=3,
            selection_strategy="cost",
        )
        assert providers[0][0] == "cheap"  # Lowest cost first
        assert providers[1][0] == "priority"
        assert providers[2][0] == "expensive"

    def test_health_tracking(self):
        """Test health metrics tracking"""
        registry = CanonicalModelRegistry()

        model = CanonicalModel(id="test-model", name="Test")
        model.add_provider(provider_name="provider1", provider_model_id="test")
        registry.register_canonical_model(model)

        # Record successful request
        registry.record_request_outcome(
            model_id="test-model",
            provider="provider1",
            success=True,
            latency_ms=100,
        )

        # Get health metrics
        metrics = registry.get_health_metrics("test-model", "provider1")
        assert "provider1" in metrics
        assert metrics["provider1"].success_count == 1
        assert metrics["provider1"].avg_latency_ms == 100.0

        # Record failure
        registry.record_request_outcome(
            model_id="test-model",
            provider="provider1",
            success=False,
        )

        metrics = registry.get_health_metrics("test-model", "provider1")
        assert metrics["provider1"].failure_count == 1
        assert metrics["provider1"].success_rate == 0.5

    def test_ingest_provider_catalog(self):
        """Test ingesting provider catalog"""
        registry = CanonicalModelRegistry()

        # Mock catalog data
        catalog = [
            {
                "id": "model-1",
                "name": "Model 1",
                "context_length": 4096,
                "modalities": ["text"],
                "features": ["streaming"],
                "pricing": {"input": 0.5, "output": 0.5},
            },
            {
                "id": "model-2",
                "name": "Model 2",
                "context_length": 8192,
                "modalities": ["text", "image"],
                "features": ["streaming", "function_calling"],
                "pricing": {"input": 1.0, "output": 2.0},
            },
        ]

        # Ingest catalog
        count = registry.ingest_provider_catalog(
            provider_name="test-provider",
            catalog=catalog,
        )

        assert count == 2

        # Check models were added
        model1 = registry.get_canonical_model("model-1")
        assert model1 is not None
        assert "test-provider" in model1.providers
        assert model1.context_lengths["test-provider"] == 4096

        model2 = registry.get_canonical_model("model-2")
        assert model2 is not None
        assert "image" in model2.modalities
        assert model2.min_input_cost == 1.0

    def test_export_catalog(self):
        """Test exporting the complete catalog"""
        registry = CanonicalModelRegistry()

        # Add test model
        model = CanonicalModel(id="export-test", name="Export Test")
        model.add_provider(
            provider_name="provider1",
            provider_model_id="test-id",
            input_cost=0.5,
            output_cost=1.0,
        )
        registry.register_canonical_model(model)

        # Add alias
        registry.add_alias("export-alias", "export-test")

        # Export catalog
        catalog = registry.export_catalog()

        assert "models" in catalog
        assert "aliases" in catalog
        assert len(catalog["models"]) == 1
        assert catalog["models"][0]["id"] == "export-test"
        assert "export-alias" in catalog["aliases"]
        assert catalog["aliases"]["export-alias"] == "export-test"


class TestProviderSelector:
    """Test the ProviderSelector with canonical registry"""

    @patch("src.services.provider_selector.get_canonical_registry")
    def test_execute_with_canonical_failover(self, mock_get_registry):
        """Test executing with failover using canonical registry"""
        from src.services.provider_selector import ProviderSelector

        # Setup mock registry
        mock_registry = MagicMock(spec=CanonicalModelRegistry)
        mock_get_registry.return_value = mock_registry

        # Setup mock canonical model
        mock_model = MagicMock(spec=CanonicalModel)
        mock_model.providers = {
            "provider1": MagicMock(enabled=True, priority=1),
            "provider2": MagicMock(enabled=True, priority=2),
        }
        mock_model.provider_model_ids = {
            "provider1": "model-v1",
            "provider2": "model-v2",
        }

        mock_registry.resolve_model_id.return_value = "canonical-id"
        mock_registry.get_canonical_model.return_value = mock_model
        mock_registry.select_providers_with_failover.return_value = [
            ("provider1", mock_model.providers["provider1"]),
            ("provider2", mock_model.providers["provider2"]),
        ]

        # Create selector
        selector = ProviderSelector()
        selector.registry = mock_registry

        # Mock execute function that fails on first provider
        call_count = 0

        def mock_execute(provider, model_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Provider 1 failed")
            return {"response": "success"}

        # Execute with failover
        result = selector.execute_with_failover(
            model_id="test-model",
            execute_fn=mock_execute,
            max_retries=2,
            record_metrics=False,
        )

        assert result["success"] is True
        assert result["response"]["response"] == "success"
        assert result["canonical_model_id"] == "canonical-id"
        assert len(result["attempts"]) == 2
        assert result["attempts"][0]["success"] is False
        assert result["attempts"][1]["success"] is True

    @patch("src.services.provider_selector.get_canonical_registry")
    def test_get_provider_recommendations(self, mock_get_registry):
        """Test getting provider recommendations"""
        from src.services.provider_selector import ProviderSelector

        # Setup mock registry
        mock_registry = MagicMock(spec=CanonicalModelRegistry)
        mock_get_registry.return_value = mock_registry

        # Setup mock model with providers
        mock_model = MagicMock(spec=CanonicalModel)
        mock_model.providers = {
            "cheap": MagicMock(
                priority=2,
                cost_per_1k_input=0.1,
                cost_per_1k_output=0.1,
                features=["streaming"],
            ),
            "fast": MagicMock(
                priority=1,
                cost_per_1k_input=0.5,
                cost_per_1k_output=0.5,
                features=["streaming", "function_calling"],
            ),
        }
        mock_model.provider_model_ids = {
            "cheap": "model-cheap",
            "fast": "model-fast",
        }

        mock_registry.resolve_model_id.return_value = "canonical-id"
        mock_registry.get_canonical_model.return_value = mock_model
        mock_registry.select_providers_with_failover.return_value = [
            ("cheap", mock_model.providers["cheap"]),
            ("fast", mock_model.providers["fast"]),
        ]
        mock_registry.get_health_metrics.return_value = {}

        # Create selector
        selector = ProviderSelector()
        selector.registry = mock_registry

        # Get recommendations optimizing for cost
        recommendations = selector.get_provider_recommendations(
            model_id="test-model",
            optimize_for="cost",
        )

        assert len(recommendations) == 2
        assert recommendations[0]["provider"] == "cheap"
        assert recommendations[0]["cost_per_1k"]["total"] == 0.2


class TestProviderFailover:
    """Test the provider failover with canonical registry"""

    @patch("src.services.provider_failover.get_canonical_registry")
    def test_build_failover_chain_with_registry(self, mock_get_registry):
        """Test building failover chain using canonical registry"""
        from src.services.provider_failover import build_provider_failover_chain

        # Setup mock registry
        mock_registry = MagicMock(spec=CanonicalModelRegistry)
        mock_get_registry.return_value = mock_registry

        # Setup mock model
        mock_model = MagicMock(spec=CanonicalModel)
        mock_registry.resolve_model_id.return_value = "canonical-id"
        mock_registry.get_canonical_model.return_value = mock_model
        mock_registry.select_providers_with_failover.return_value = [
            ("provider1", MagicMock()),
            ("provider2", MagicMock()),
            ("provider3", MagicMock()),
        ]

        # Build chain
        chain = build_provider_failover_chain(
            initial_provider=None,
            model_id="test-model",
            use_registry=True,
        )

        assert len(chain) == 3
        assert chain == ["provider1", "provider2", "provider3"]

    def test_build_failover_chain_legacy(self):
        """Test building failover chain with legacy behavior"""
        from src.services.provider_failover import build_provider_failover_chain

        # Test with eligible provider
        chain = build_provider_failover_chain(
            initial_provider="huggingface",
            model_id=None,
            use_registry=False,
        )

        assert chain[0] == "huggingface"
        assert "openrouter" in chain
        assert len(chain) > 1

        # Test with non-eligible provider
        chain = build_provider_failover_chain(
            initial_provider="custom-provider",
            model_id=None,
            use_registry=False,
        )

        assert chain == ["custom-provider"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])