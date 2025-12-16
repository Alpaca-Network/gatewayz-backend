"""
Tests for Model Sync Routes (src/routes/model_sync.py)

Tests admin endpoints for synchronizing provider models from their APIs to database:
- List available providers
- Sync single provider
- Sync all providers
- Get sync status
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.main import create_app
from tests.helpers.mocks import mock_user


@pytest.fixture
def app():
    """Create FastAPI test app"""
    return create_app()


@pytest.fixture
def client(app):
    """Create test client"""
    return TestClient(app)


@pytest.fixture
def admin_headers(monkeypatch):
    """Mock admin authentication"""
    admin_key = "test-admin-key-12345"
    monkeypatch.setenv("ADMIN_API_KEY", admin_key)
    return {"Authorization": f"Bearer {admin_key}"}


@pytest.fixture
def mock_provider_functions(monkeypatch):
    """Mock PROVIDER_FETCH_FUNCTIONS"""
    mock_providers = {
        "openrouter": MagicMock(),
        "deepinfra": MagicMock(),
        "anthropic": MagicMock(),
    }

    import src.routes.model_sync as model_sync_module
    monkeypatch.setattr(
        model_sync_module,
        "PROVIDER_FETCH_FUNCTIONS",
        mock_providers
    )
    return mock_providers


# ============================================================================
# List Providers Tests
# ============================================================================

class TestListProviders:
    """Test GET /admin/model-sync/providers"""

    @pytest.mark.unit
    def test_list_providers_success(self, client, admin_headers, mock_provider_functions):
        """Should return list of available providers"""
        response = client.get("/admin/model-sync/providers", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()

        assert "providers" in data
        assert "count" in data
        assert isinstance(data["providers"], list)
        assert data["count"] == len(data["providers"])
        assert "openrouter" in data["providers"]
        assert "deepinfra" in data["providers"]
        assert "anthropic" in data["providers"]

    @pytest.mark.unit
    def test_list_providers_unauthorized(self, client):
        """Should reject request without admin key"""
        response = client.get("/admin/model-sync/providers")

        assert response.status_code == 403  # No Authorization header

    @pytest.mark.unit
    def test_list_providers_invalid_admin_key(self, client, monkeypatch):
        """Should reject request with invalid admin key"""
        monkeypatch.setenv("ADMIN_API_KEY", "correct-key")

        headers = {"Authorization": "Bearer wrong-key"}
        response = client.get("/admin/model-sync/providers", headers=headers)

        assert response.status_code == 401


# ============================================================================
# Sync Single Provider Tests
# ============================================================================

class TestSyncSingleProvider:
    """Test POST /admin/model-sync/provider/{provider_slug}"""

    @pytest.mark.unit
    def test_sync_provider_success(self, client, admin_headers, mock_provider_functions):
        """Should successfully sync provider models"""
        with patch("src.routes.model_sync.sync_provider_models") as mock_sync:
            mock_sync.return_value = {
                "success": True,
                "models_fetched": 100,
                "models_transformed": 95,
                "models_synced": 95,
                "models_skipped": 5,
            }

            response = client.post(
                "/admin/model-sync/provider/openrouter",
                headers=admin_headers
            )

            assert response.status_code == 200
            data = response.json()

            assert data["success"] is True
            assert "message" in data
            assert "details" in data
            assert "Synced 95 models" in data["message"]
            assert mock_sync.called
            assert mock_sync.call_args[0][0] == "openrouter"
            assert mock_sync.call_args[1]["dry_run"] is False

    @pytest.mark.unit
    def test_sync_provider_dry_run(self, client, admin_headers, mock_provider_functions):
        """Should perform dry run without writing to database"""
        with patch("src.routes.model_sync.sync_provider_models") as mock_sync:
            mock_sync.return_value = {
                "success": True,
                "models_fetched": 100,
                "models_transformed": 95,
                "models_synced": 0,  # No syncing in dry run
                "models_skipped": 5,
            }

            response = client.post(
                "/admin/model-sync/provider/openrouter?dry_run=true",
                headers=admin_headers
            )

            assert response.status_code == 200
            data = response.json()

            assert data["success"] is True
            assert "[DRY RUN]" in data["message"]
            assert mock_sync.call_args[1]["dry_run"] is True

    @pytest.mark.unit
    def test_sync_provider_not_found(self, client, admin_headers, mock_provider_functions):
        """Should return 404 for unknown provider"""
        response = client.post(
            "/admin/model-sync/provider/unknown_provider",
            headers=admin_headers
        )

        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()
        assert "Available providers" in data["detail"]

    @pytest.mark.unit
    def test_sync_provider_sync_failure(self, client, admin_headers, mock_provider_functions):
        """Should return 500 if sync fails"""
        with patch("src.routes.model_sync.sync_provider_models") as mock_sync:
            mock_sync.return_value = {
                "success": False,
                "error": "Failed to fetch models from API"
            }

            response = client.post(
                "/admin/model-sync/provider/openrouter",
                headers=admin_headers
            )

            assert response.status_code == 500
            data = response.json()
            assert "Failed to fetch models" in data["detail"]

    @pytest.mark.unit
    def test_sync_provider_unauthorized(self, client, mock_provider_functions):
        """Should reject request without admin key"""
        response = client.post("/admin/model-sync/provider/openrouter")

        assert response.status_code == 403


# ============================================================================
# Sync All Providers Tests
# ============================================================================

class TestSyncAllProviders:
    """Test POST /admin/model-sync/all"""

    @pytest.mark.unit
    def test_sync_all_success(self, client, admin_headers, mock_provider_functions):
        """Should successfully sync all providers"""
        with patch("src.routes.model_sync.sync_all_providers") as mock_sync:
            mock_sync.return_value = {
                "success": True,
                "providers_processed": 3,
                "total_models_fetched": 300,
                "total_models_transformed": 285,
                "total_models_synced": 285,
                "total_models_skipped": 15,
                "errors": [],
            }

            response = client.post("/admin/model-sync/all", headers=admin_headers)

            assert response.status_code == 200
            data = response.json()

            assert data["success"] is True
            assert "Processed 3 providers" in data["message"]
            assert "Success: 3" in data["message"]
            assert "Errors: 0" in data["message"]
            assert mock_sync.called
            assert mock_sync.call_args[1]["provider_slugs"] is None
            assert mock_sync.call_args[1]["dry_run"] is False

    @pytest.mark.unit
    def test_sync_all_with_specific_providers(self, client, admin_headers, mock_provider_functions):
        """Should sync only specified providers"""
        with patch("src.routes.model_sync.sync_all_providers") as mock_sync:
            mock_sync.return_value = {
                "success": True,
                "providers_processed": 2,
                "total_models_fetched": 200,
                "total_models_transformed": 190,
                "total_models_synced": 190,
                "total_models_skipped": 10,
                "errors": [],
            }

            response = client.post(
                "/admin/model-sync/all?providers=openrouter&providers=anthropic",
                headers=admin_headers
            )

            assert response.status_code == 200
            data = response.json()

            assert data["success"] is True
            assert mock_sync.call_args[1]["provider_slugs"] == ["openrouter", "anthropic"]

    @pytest.mark.unit
    def test_sync_all_with_errors(self, client, admin_headers, mock_provider_functions):
        """Should report errors for failed providers"""
        with patch("src.routes.model_sync.sync_all_providers") as mock_sync:
            mock_sync.return_value = {
                "success": False,
                "providers_processed": 3,
                "total_models_fetched": 200,
                "total_models_transformed": 190,
                "total_models_synced": 190,
                "total_models_skipped": 10,
                "errors": ["deepinfra: Connection timeout"],
            }

            response = client.post("/admin/model-sync/all", headers=admin_headers)

            assert response.status_code == 200  # Still returns 200 with errors
            data = response.json()

            assert data["success"] is False
            assert "Success: 2" in data["message"]
            assert "Errors: 1" in data["message"]
            assert "errors" in data["details"]

    @pytest.mark.unit
    def test_sync_all_invalid_provider(self, client, admin_headers, mock_provider_functions):
        """Should reject invalid provider names"""
        response = client.post(
            "/admin/model-sync/all?providers=invalid_provider",
            headers=admin_headers
        )

        assert response.status_code == 400
        data = response.json()
        assert "Invalid providers" in data["detail"]

    @pytest.mark.unit
    def test_sync_all_dry_run(self, client, admin_headers, mock_provider_functions):
        """Should perform dry run without writing to database"""
        with patch("src.routes.model_sync.sync_all_providers") as mock_sync:
            mock_sync.return_value = {
                "success": True,
                "providers_processed": 3,
                "total_models_fetched": 300,
                "total_models_transformed": 285,
                "total_models_synced": 0,  # No syncing in dry run
                "total_models_skipped": 15,
                "errors": [],
            }

            response = client.post(
                "/admin/model-sync/all?dry_run=true",
                headers=admin_headers
            )

            assert response.status_code == 200
            data = response.json()

            assert data["success"] is True
            assert "[DRY RUN]" in data["message"]
            assert mock_sync.call_args[1]["dry_run"] is True

    @pytest.mark.unit
    def test_sync_all_catastrophic_failure(self, client, admin_headers, mock_provider_functions):
        """Should return 500 if no providers were processed"""
        with patch("src.routes.model_sync.sync_all_providers") as mock_sync:
            mock_sync.return_value = {
                "success": False,
                "providers_processed": 0,
                "error": "Database connection failed"
            }

            response = client.post("/admin/model-sync/all", headers=admin_headers)

            assert response.status_code == 500
            data = response.json()
            assert "Database connection failed" in data["detail"]


# ============================================================================
# Get Sync Status Tests
# ============================================================================

class TestGetSyncStatus:
    """Test GET /admin/model-sync/status"""

    @pytest.mark.unit
    def test_get_status_success(self, client, admin_headers, mock_provider_functions):
        """Should return sync status and statistics"""
        with patch("src.routes.model_sync.get_all_providers") as mock_get_providers:
            with patch("src.routes.model_sync.get_providers_stats") as mock_provider_stats:
                with patch("src.routes.model_sync.get_models_stats") as mock_model_stats:
                    # Mock database responses
                    mock_get_providers.return_value = [
                        {"slug": "openrouter", "is_active": True},
                        {"slug": "deepinfra", "is_active": True},
                        {"slug": "anthropic", "is_active": False},
                    ]

                    mock_provider_stats.return_value = {
                        "total": 3,
                        "active": 2,
                        "inactive": 1,
                    }

                    mock_model_stats.return_value = {
                        "total": 500,
                        "active": 450,
                        "inactive": 50,
                    }

                    response = client.get("/admin/model-sync/status", headers=admin_headers)

                    assert response.status_code == 200
                    data = response.json()

                    assert "providers" in data
                    assert "models" in data
                    assert "fetchable_providers" in data
                    assert "fetchable_in_db" in data
                    assert "fetchable_not_in_db" in data

                    # Check providers info
                    assert data["providers"]["in_database"] == 3
                    assert data["providers"]["with_fetch_functions"] == 3

                    # Check fetchable providers
                    assert "openrouter" in data["fetchable_providers"]
                    assert "deepinfra" in data["fetchable_providers"]

    @pytest.mark.unit
    def test_get_status_unauthorized(self, client):
        """Should reject request without admin key"""
        response = client.get("/admin/model-sync/status")

        assert response.status_code == 403

    @pytest.mark.unit
    def test_get_status_database_error(self, client, admin_headers):
        """Should return 500 if database query fails"""
        with patch(
            "src.routes.model_sync.get_all_providers",
            side_effect=Exception("Database connection failed")
        ):
            response = client.get("/admin/model-sync/status", headers=admin_headers)

            assert response.status_code == 500
            data = response.json()
            assert "Database connection failed" in data["detail"]


# ============================================================================
# Integration Tests
# ============================================================================

class TestModelSyncIntegration:
    """Integration tests for model sync workflows"""

    @pytest.mark.integration
    def test_full_sync_workflow(self, client, admin_headers, mock_provider_functions):
        """Test complete sync workflow: list -> sync -> status"""
        with patch("src.routes.model_sync.sync_provider_models") as mock_sync:
            with patch("src.routes.model_sync.get_all_providers") as mock_get_providers:
                with patch("src.routes.model_sync.get_providers_stats") as mock_provider_stats:
                    with patch("src.routes.model_sync.get_models_stats") as mock_model_stats:
                        # Setup mocks
                        mock_sync.return_value = {
                            "success": True,
                            "models_fetched": 100,
                            "models_transformed": 95,
                            "models_synced": 95,
                            "models_skipped": 5,
                        }

                        mock_get_providers.return_value = [
                            {"slug": "openrouter", "is_active": True}
                        ]

                        mock_provider_stats.return_value = {"total": 1, "active": 1}
                        mock_model_stats.return_value = {"total": 95, "active": 95}

                        # Step 1: List providers
                        response1 = client.get("/admin/model-sync/providers", headers=admin_headers)
                        assert response1.status_code == 200
                        providers = response1.json()["providers"]
                        assert "openrouter" in providers

                        # Step 2: Sync provider
                        response2 = client.post(
                            "/admin/model-sync/provider/openrouter",
                            headers=admin_headers
                        )
                        assert response2.status_code == 200
                        assert response2.json()["success"] is True

                        # Step 3: Check status
                        response3 = client.get("/admin/model-sync/status", headers=admin_headers)
                        assert response3.status_code == 200
                        status = response3.json()
                        assert status["models"]["stats"]["total"] == 95
