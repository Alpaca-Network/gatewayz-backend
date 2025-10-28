#!/usr/bin/env python3
"""
Tests for the new fix_gateway endpoint and dashboard pricing features
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from fastapi import HTTPException

from src.main import app


@pytest.fixture
def client():
    """FastAPI test client"""
    return TestClient(app)


class TestFixGatewayEndpoint:
    """Test the new fix_gateway endpoint"""

    @patch('src.routes.system.get_models_cache')
    @patch('src.routes.system.fetch_models_from_openrouter')
    @patch('src.routes.system.clear_models_cache')
    @patch('src.routes.system.run_comprehensive_check', MagicMock())
    def test_fix_gateway_success(
        self,
        mock_clear,
        mock_fetch,
        mock_get_cache,
        client
    ):
        """Test successful gateway fix"""
        # Setup mocks
        mock_fetch.return_value = None  # Fetch completes successfully
        mock_get_cache.return_value = {
            "data": [{"id": "model1"}, {"id": "model2"}],
            "timestamp": datetime.now(timezone.utc)
        }

        response = client.post('/health/gateways/openrouter/fix')

        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert data['gateway'] == 'openrouter'
        assert data['models_count'] == 2
        assert 'timestamp' in data

        # Verify calls
        mock_clear.assert_called_once_with('openrouter')
        mock_fetch.assert_called_once()

    @patch('src.routes.system.run_comprehensive_check', None)
    def test_fix_gateway_module_unavailable(
        self,
        client
    ):
        """Test when check_and_fix_gateway_models module is unavailable"""

        response = client.post('/health/gateways/openrouter/fix')

        assert response.status_code == 503
        data = response.json()
        assert 'unavailable' in data['detail']

    @patch('src.routes.system.clear_models_cache')
    @patch('src.routes.system.run_comprehensive_check', MagicMock())
    def test_fix_gateway_unknown_gateway(
        self,
        mock_clear,
        client
    ):
        """Test fixing an unknown gateway"""

        response = client.post('/health/gateways/unknown_gateway/fix')

        assert response.status_code == 400
        data = response.json()
        assert 'Unknown gateway' in data['detail']

    @patch('src.routes.system.get_models_cache')
    @patch('src.routes.system.fetch_models_from_portkey')
    @patch('src.routes.system.clear_models_cache')
    @patch('src.routes.system.run_comprehensive_check', MagicMock())
    def test_fix_gateway_fetch_error(
        self,
        mock_clear,
        mock_fetch,
        mock_get_cache,
        client
    ):
        """Test when fetch fails"""
        mock_fetch.side_effect = Exception("Fetch failed")
        mock_get_cache.return_value = None

        response = client.post('/health/gateways/portkey/fix')

        assert response.status_code == 200  # Returns 200 with error message
        data = response.json()
        assert data['success'] is False
        assert 'Failed to fetch models' in data['message']


class TestDashboardPricingFeatures:
    """Test dashboard endpoints with pricing features"""

    @patch('src.routes.system._run_gateway_check')
    @patch('src.routes.system.load_manual_pricing')
    @patch('src.routes.system.get_model_pricing')
    async def test_dashboard_data_with_pricing(
        self,
        mock_get_pricing,
        mock_load_pricing,
        mock_run_check,
        client
    ):
        """Test dashboard data endpoint includes pricing"""
        # Setup mocks
        mock_run_check.return_value = (
            {
                "timestamp": "2025-01-15T10:00:00Z",
                "total_gateways": 1,
                "healthy": 1,
                "unhealthy": 0,
                "unconfigured": 0,
                "fixed": 0,
                "gateways": {
                    "openrouter": {
                        "name": "OpenRouter",
                        "configured": True,
                        "cache_test": {
                            "models": [
                                {"id": "gpt-4"},
                                {"id": "claude-3"}
                            ]
                        }
                    }
                }
            },
            "Log output"
        )

        mock_load_pricing.return_value = {"openrouter": {}}
        mock_get_pricing.return_value = {
            "prompt": "0.03",
            "completion": "0.06"
        }

        response = client.get('/health/gateways/dashboard/data?include_pricing=true')

        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True

        # Check that models have pricing
        gateway_data = data['gateways']['openrouter']
        models = gateway_data['cache_test']['models']
        assert len(models) == 2
        assert 'pricing' in models[0]
        assert models[0]['pricing']['prompt'] == "0.03"

    @patch('src.routes.system._run_gateway_check')
    @patch('src.routes.system.load_manual_pricing')
    async def test_dashboard_data_without_pricing(
        self,
        mock_load_pricing,
        mock_run_check,
        client
    ):
        """Test dashboard data endpoint without pricing"""
        mock_run_check.return_value = (
            {
                "timestamp": "2025-01-15T10:00:00Z",
                "total_gateways": 1,
                "healthy": 1,
                "unhealthy": 0,
                "unconfigured": 0,
                "fixed": 0,
                "gateways": {
                    "openrouter": {
                        "name": "OpenRouter",
                        "configured": True,
                        "cache_test": {
                            "models": ["model1", "model2"]
                        }
                    }
                }
            },
            "Log output"
        )

        response = client.get('/health/gateways/dashboard/data?include_pricing=false')

        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True

        # Pricing should not be loaded when include_pricing=false
        mock_load_pricing.assert_not_called()

    @patch('src.routes.system._run_gateway_check')
    @patch('src.routes.system.load_manual_pricing')
    async def test_dashboard_html_with_pricing(
        self,
        mock_load_pricing,
        mock_run_check,
        client
    ):
        """Test dashboard HTML endpoint includes pricing in rendered HTML"""
        mock_run_check.return_value = (
            {
                "timestamp": "2025-01-15T10:00:00Z",
                "total_gateways": 1,
                "healthy": 0,
                "unhealthy": 1,
                "unconfigured": 0,
                "fixed": 0,
                "gateways": {
                    "deepinfra": {
                        "name": "DeepInfra",
                        "configured": True,
                        "final_status": "unhealthy",
                        "cache_test": {
                            "models": [
                                {"id": "meta-llama/Meta-Llama-3.1-8B-Instruct"}
                            ]
                        }
                    }
                }
            },
            "Log output"
        )

        mock_load_pricing.return_value = {
            "deepinfra": {
                "meta-llama/Meta-Llama-3.1-8B-Instruct": {
                    "prompt": "0.055",
                    "completion": "0.055"
                }
            }
        }

        response = client.get('/health/gateways/dashboard')

        assert response.status_code == 200
        html_content = response.text

        # Check that HTML contains pricing information
        assert 'Input: $' in html_content
        assert 'Output: $' in html_content
        assert 'Fix Gateway' in html_content  # Fix button should be present
        assert 'fixGateway' in html_content  # JavaScript function should be present

    @patch('src.routes.system.run_comprehensive_check', None)
    async def test_dashboard_unavailable(
        self,
        client
    ):
        """Test dashboard when check module is unavailable"""

        response = client.get('/health/gateways/dashboard')

        assert response.status_code == 503
        data = response.json()
        assert 'unavailable' in data['detail']