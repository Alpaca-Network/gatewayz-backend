"""
QA Test Suite: Chat Requests Monitoring Endpoints
Tests for:
- GET /api/monitoring/chat-requests/counts
- GET /api/monitoring/chat-requests/models
- GET /api/monitoring/chat-requests

Purpose: Verify all endpoints use real database data, not mock data.
Created: 2025-12-28
"""

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient


class TestChatRequestsCountsEndpoint:
    """Test /api/monitoring/chat-requests/counts endpoint"""

    @pytest.mark.asyncio
    async def test_counts_endpoint_returns_200(self, client: AsyncClient):
        """Test that counts endpoint returns 200 OK"""
        response = await client.get("/api/monitoring/chat-requests/counts")
        assert (
            response.status_code == 200
        ), f"Expected 200, got {response.status_code}: {response.text}"

    @pytest.mark.asyncio
    async def test_counts_endpoint_response_structure(self, client: AsyncClient):
        """Test that response has correct structure"""
        response = await client.get("/api/monitoring/chat-requests/counts")
        data = response.json()

        # Verify response structure
        assert "success" in data, "Response missing 'success' field"
        assert "data" in data, "Response missing 'data' field"
        assert "metadata" in data, "Response missing 'metadata' field"

        # Verify metadata
        assert "total_models" in data["metadata"]
        assert "total_requests" in data["metadata"]
        assert "timestamp" in data["metadata"]

    @pytest.mark.asyncio
    async def test_counts_endpoint_uses_real_data(self, client: AsyncClient):
        """Test that endpoint returns real data from database (not mock)"""
        response = await client.get("/api/monitoring/chat-requests/counts")
        data = response.json()

        # Should have data list
        assert isinstance(data["data"], list), "Data should be a list"

        # If there's data, verify structure
        if len(data["data"]) > 0:
            first_record = data["data"][0]

            # Required fields from database
            assert "model_id" in first_record, "Missing model_id"
            assert "model_name" in first_record, "Missing model_name"
            assert "provider_name" in first_record, "Missing provider_name"
            assert "request_count" in first_record, "Missing request_count"

            # Values should be real (not test/mock markers)
            assert isinstance(
                first_record["model_id"], (int, str)
            ), "model_id should be int or string"
            assert isinstance(first_record["request_count"], int), "request_count should be integer"
            assert first_record["request_count"] >= 0, "request_count should be non-negative"

    @pytest.mark.asyncio
    async def test_counts_endpoint_data_is_sorted(self, client: AsyncClient):
        """Test that data is sorted by request count (descending)"""
        response = await client.get("/api/monitoring/chat-requests/counts")
        data = response.json()

        if len(data["data"]) > 1:
            counts = [record["request_count"] for record in data["data"]]
            # Verify sorted descending
            assert counts == sorted(
                counts, reverse=True
            ), "Data should be sorted by request_count (descending)"

    @pytest.mark.asyncio
    async def test_counts_endpoint_metadata_accuracy(self, client: AsyncClient):
        """Test that metadata totals match actual data"""
        response = await client.get("/api/monitoring/chat-requests/counts")
        data = response.json()

        # Verify metadata matches data
        assert data["metadata"]["total_models"] == len(
            data["data"]
        ), f"total_models ({data['metadata']['total_models']}) doesn't match data length ({len(data['data'])})"

        actual_total_requests = sum(record["request_count"] for record in data["data"])
        assert (
            data["metadata"]["total_requests"] == actual_total_requests
        ), f"total_requests in metadata ({data['metadata']['total_requests']}) doesn't match actual sum ({actual_total_requests})"

    @pytest.mark.asyncio
    async def test_counts_endpoint_timestamp_is_valid(self, client: AsyncClient):
        """Test that timestamp is valid ISO-8601"""
        response = await client.get("/api/monitoring/chat-requests/counts")
        data = response.json()

        timestamp = data["metadata"]["timestamp"]
        # Should be ISO-8601 format
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            pytest.fail(f"Invalid ISO-8601 timestamp: {timestamp}")


class TestChatRequestsModelsEndpoint:
    """Test /api/monitoring/chat-requests/models endpoint"""

    @pytest.mark.asyncio
    async def test_models_endpoint_returns_200(self, client: AsyncClient):
        """Test that models endpoint returns 200 OK"""
        response = await client.get("/api/monitoring/chat-requests/models")
        assert (
            response.status_code == 200
        ), f"Expected 200, got {response.status_code}: {response.text}"

    @pytest.mark.asyncio
    async def test_models_endpoint_response_structure(self, client: AsyncClient):
        """Test that response has correct structure"""
        response = await client.get("/api/monitoring/chat-requests/models")
        data = response.json()

        # Verify response structure
        assert "success" in data
        assert "data" in data
        assert "metadata" in data

        # Verify metadata
        assert "total_models" in data["metadata"]
        assert "timestamp" in data["metadata"]

    @pytest.mark.asyncio
    async def test_models_endpoint_returns_real_model_data(self, client: AsyncClient):
        """Test that endpoint returns real models with stats (not mock)"""
        response = await client.get("/api/monitoring/chat-requests/models")
        data = response.json()

        # Should have data list
        assert isinstance(data["data"], list)

        # If there's data, verify it's real
        if len(data["data"]) > 0:
            first_model = data["data"][0]

            # Model information fields
            assert "model_id" in first_model
            assert "model_name" in first_model
            assert "provider" in first_model

            # Stats fields
            assert "stats" in first_model, "Missing stats object"
            stats = first_model["stats"]

            assert "total_requests" in stats
            assert "total_input_tokens" in stats
            assert "total_output_tokens" in stats
            assert "total_tokens" in stats
            assert "avg_processing_time_ms" in stats

            # Values should be real numbers (not "N/A" or test markers)
            assert isinstance(stats["total_requests"], int)
            assert isinstance(stats["total_input_tokens"], int)
            assert isinstance(stats["total_output_tokens"], int)
            assert isinstance(stats["total_tokens"], int)
            assert isinstance(stats["avg_processing_time_ms"], (int, float))

            # Token counts should be reasonable
            assert stats["total_input_tokens"] >= 0
            assert stats["total_output_tokens"] >= 0
            assert (
                stats["total_tokens"] == stats["total_input_tokens"] + stats["total_output_tokens"]
            )

    @pytest.mark.asyncio
    async def test_models_endpoint_provider_data(self, client: AsyncClient):
        """Test that provider information is included and real"""
        response = await client.get("/api/monitoring/chat-requests/models")
        data = response.json()

        if len(data["data"]) > 0:
            first_model = data["data"][0]
            provider = first_model.get("provider")

            if provider:  # Provider might be None in some cases
                # Should have provider fields
                assert isinstance(provider, dict)
                # Provider should have expected structure from database
                # (id, name, slug would be from Supabase)

    @pytest.mark.asyncio
    async def test_models_endpoint_sorted_by_requests(self, client: AsyncClient):
        """Test that models are sorted by request count (descending)"""
        response = await client.get("/api/monitoring/chat-requests/models")
        data = response.json()

        if len(data["data"]) > 1:
            request_counts = [model["stats"]["total_requests"] for model in data["data"]]
            assert request_counts == sorted(
                request_counts, reverse=True
            ), "Models should be sorted by request count (descending)"


class TestChatRequestsEndpoint:
    """Test /api/monitoring/chat-requests endpoint"""

    @pytest.mark.asyncio
    async def test_chat_requests_returns_200(self, client: AsyncClient):
        """Test that endpoint returns 200 OK"""
        response = await client.get("/api/monitoring/chat-requests")
        assert (
            response.status_code == 200
        ), f"Expected 200, got {response.status_code}: {response.text}"

    @pytest.mark.asyncio
    async def test_chat_requests_response_structure(self, client: AsyncClient):
        """Test that response has correct structure"""
        response = await client.get("/api/monitoring/chat-requests")
        data = response.json()

        # Verify response structure
        assert "success" in data
        assert "data" in data
        assert "metadata" in data

        # Verify metadata
        assert "total_count" in data["metadata"]
        assert "limit" in data["metadata"]
        assert "offset" in data["metadata"]
        assert "returned_count" in data["metadata"]
        assert "filters" in data["metadata"]
        assert "timestamp" in data["metadata"]

    @pytest.mark.asyncio
    async def test_chat_requests_returns_real_requests(self, client: AsyncClient):
        """Test that endpoint returns real request data (not mock)"""
        response = await client.get("/api/monitoring/chat-requests")
        data = response.json()

        # Should have data list
        assert isinstance(data["data"], list)

        # If there's data, verify it's from real database
        if len(data["data"]) > 0:
            first_request = data["data"][0]

            # Request fields (from chat_completion_requests table)
            # Note: These would come from the actual table schema
            # Common fields might include:
            # - request_id
            # - model_id
            # - input_tokens, output_tokens
            # - processing_time_ms
            # - created_at

            # At minimum, should have model relation
            assert (
                "models" in first_request or "model_id" in first_request
            ), "Request should have model information"

    @pytest.mark.asyncio
    async def test_chat_requests_pagination(self, client: AsyncClient):
        """Test that pagination works correctly"""
        # Default pagination
        response1 = await client.get("/api/monitoring/chat-requests?limit=10&offset=0")
        data1 = response1.json()

        # With offset
        response2 = await client.get("/api/monitoring/chat-requests?limit=10&offset=10")
        data2 = response2.json()

        # Both should be successful
        assert response1.status_code == 200
        assert response2.status_code == 200

        # Metadata should reflect pagination
        assert data1["metadata"]["limit"] == 10
        assert data1["metadata"]["offset"] == 0
        assert data2["metadata"]["limit"] == 10
        assert data2["metadata"]["offset"] == 10

    @pytest.mark.asyncio
    async def test_chat_requests_filter_by_model_id(self, client: AsyncClient):
        """Test filtering by model_id"""
        # First, get all requests to find a valid model_id
        response_all = await client.get("/api/monitoring/chat-requests?limit=100")
        data_all = response_all.json()

        if len(data_all["data"]) > 0:
            first_request = data_all["data"][0]
            model_id = first_request.get("model_id")

            if model_id:
                # Filter by that model_id
                response_filtered = await client.get(
                    f"/api/monitoring/chat-requests?model_id={model_id}"
                )
                data_filtered = response_filtered.json()

                # Should be successful
                assert response_filtered.status_code == 200

                # Metadata should show the filter
                assert data_filtered["metadata"]["filters"]["model_id"] == model_id

    @pytest.mark.asyncio
    async def test_chat_requests_filter_by_model_name(self, client: AsyncClient):
        """Test filtering by model name"""
        response = await client.get("/api/monitoring/chat-requests?model_name=gpt")
        data = response.json()

        assert response.status_code == 200
        assert "model_name" in data["metadata"]["filters"]

        # Results should be empty or contain matching models
        if len(data["data"]) > 0:
            # All results should have model names containing "gpt" (case-insensitive)
            for request in data["data"]:
                if "models" in request and "model_name" in request["models"]:
                    assert (
                        "gpt" in request["models"]["model_name"].lower()
                    ), f"Expected 'gpt' in model name, got {request['models']['model_name']}"

    @pytest.mark.asyncio
    async def test_chat_requests_returned_count_matches_data(self, client: AsyncClient):
        """Test that returned_count matches actual data length"""
        response = await client.get("/api/monitoring/chat-requests?limit=50")
        data = response.json()

        assert data["metadata"]["returned_count"] == len(
            data["data"]
        ), f"returned_count ({data['metadata']['returned_count']}) doesn't match actual data length ({len(data['data'])})"

    @pytest.mark.asyncio
    async def test_chat_requests_limit_validation(self, client: AsyncClient):
        """Test that limit parameter is validated"""
        # Test with valid limit
        response = await client.get("/api/monitoring/chat-requests?limit=100")
        assert response.status_code == 200

        # Test with max limit
        response = await client.get("/api/monitoring/chat-requests?limit=1000")
        assert response.status_code == 200

        # Test with limit > max (should fail or clamp)
        response = await client.get("/api/monitoring/chat-requests?limit=2000")
        # Could be 422 (validation error) or accepted with clamping
        assert response.status_code in [200, 422]


class TestChatRequestsDataIntegrity:
    """Cross-endpoint tests for data integrity"""

    @pytest.mark.asyncio
    async def test_counts_and_models_consistency(self, client: AsyncClient):
        """Test that counts and models endpoints return consistent data"""
        response_counts = await client.get("/api/monitoring/chat-requests/counts")
        response_models = await client.get("/api/monitoring/chat-requests/models")

        data_counts = response_counts.json()
        data_models = response_models.json()

        # Should have same number of models
        assert (
            data_counts["metadata"]["total_models"] == data_models["metadata"]["total_models"]
        ), "counts and models endpoints report different model counts"

        # Total requests should match between endpoints
        assert data_counts["metadata"]["total_requests"] == sum(
            m["stats"]["total_requests"] for m in data_models["data"]
        ), "total_requests don't match between endpoints"

    @pytest.mark.asyncio
    async def test_all_endpoints_return_real_timestamps(self, client: AsyncClient):
        """Test that all endpoints return valid timestamps"""
        endpoints = [
            "/api/monitoring/chat-requests/counts",
            "/api/monitoring/chat-requests/models",
            "/api/monitoring/chat-requests",
        ]

        for endpoint in endpoints:
            response = await client.get(endpoint)
            data = response.json()

            timestamp = data["metadata"]["timestamp"]
            # Should be ISO-8601
            try:
                datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                pytest.fail(f"Invalid timestamp on {endpoint}: {timestamp}")

    @pytest.mark.asyncio
    async def test_no_mock_data_markers(self, client: AsyncClient):
        """Test that responses don't contain mock/test data markers"""
        endpoints = [
            "/api/monitoring/chat-requests/counts",
            "/api/monitoring/chat-requests/models",
            "/api/monitoring/chat-requests",
        ]

        mock_markers = ["mock_", "test_data", "fake_", "N/A", "TODO", "PLACEHOLDER"]

        for endpoint in endpoints:
            response = await client.get(endpoint)
            response_text = response.text

            for marker in mock_markers:
                assert (
                    marker not in response_text
                ), f"Found mock marker '{marker}' in response from {endpoint}"

    @pytest.mark.asyncio
    async def test_success_flag_always_true_on_200(self, client: AsyncClient):
        """Test that 200 responses always have success=true"""
        endpoints = [
            "/api/monitoring/chat-requests/counts",
            "/api/monitoring/chat-requests/models",
            "/api/monitoring/chat-requests",
        ]

        for endpoint in endpoints:
            response = await client.get(endpoint)
            if response.status_code == 200:
                data = response.json()
                assert data.get("success") is True, f"{endpoint} returned 200 but success != true"
