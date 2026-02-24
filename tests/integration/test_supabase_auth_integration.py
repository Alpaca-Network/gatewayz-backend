"""
Integration tests for Supabase authentication via httpx client.

These tests verify that the custom httpx client configuration in supabase_config.py
correctly authenticates with Supabase. They make REAL API calls and will fail
if authentication headers are missing or incorrect.

This test file was created to catch the bug where custom httpx client injection
lost the apikey and Authorization headers, causing "No API key found in request" errors.

To run these tests locally or in CI:
    SUPABASE_URL=https://xxx.supabase.co SUPABASE_KEY=xxx pytest tests/integration/test_supabase_auth_integration.py -v

Mark: integration, auth
"""

import os

import httpx
import pytest

from src.config.config import Config


# Check for REAL Supabase credentials (not the fake test defaults from conftest.py)
# The conftest.py sets fake defaults like 'https://xxxxxxxxxxxxx.supabase.co'
def _has_real_supabase_credentials() -> bool:
    """Check if real Supabase credentials are available (not fake test defaults)."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")

    # Check if URL is the fake conftest default or empty
    if not url or "xxxxxxxxxxxxx" in url or not url.startswith(("http://", "https://")):
        return False

    # Check if key is the fake conftest default or empty
    if not key or "xxxxxxxxxx" in key:
        return False

    return True


# Skip entire module if real Supabase credentials are not available
pytestmark = [
    pytest.mark.integration,
    pytest.mark.auth,
    pytest.mark.skipif(
        not _has_real_supabase_credentials(),
        reason="Real SUPABASE_URL and SUPABASE_KEY required. Set env vars with actual credentials to run these tests.",
    ),
]


class TestSupabaseHttpxClientAuth:
    """
    Integration tests that verify the httpx client can authenticate with Supabase.

    These tests make REAL HTTP requests to Supabase to ensure authentication works.
    They specifically test the configuration pattern used in supabase_config.py.
    """

    def test_httpx_client_with_auth_headers_can_query_supabase(self):
        """
        Test that an httpx client configured like our production client can query Supabase.

        This test replicates the exact httpx client configuration from supabase_config.py
        and verifies it can successfully authenticate and query the database.

        If this test fails with "No API key found in request", the httpx client
        configuration is missing required authentication headers.
        """
        # Build the PostgREST base URL exactly like supabase_config.py does
        postgrest_base_url = f"{Config.SUPABASE_URL}/rest/v1"

        # Create httpx client with the SAME configuration as supabase_config.py
        # This is the critical part - headers MUST include apikey and Authorization
        httpx_client = httpx.Client(
            base_url=postgrest_base_url,
            headers={
                "apikey": Config.SUPABASE_KEY,
                "Authorization": f"Bearer {Config.SUPABASE_KEY}",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=30.0,
            ),
            http2=True,
        )

        try:
            # Make a simple query to the users table (limit 1 to minimize data transfer)
            # This is the same test query used in supabase_config.py's test_connection()
            response = httpx_client.get("/users", params={"select": "*", "limit": "1"})

            # If auth headers are missing, we get a 401 or 403 with the error:
            # {'message': 'No API key found in request', 'hint': 'No `apikey` request header or url param was found.'}
            assert response.status_code == 200, (
                f"Expected 200, got {response.status_code}. "
                f"Response: {response.text}. "
                "If you see 'No API key found in request', the httpx client is missing auth headers."
            )
        finally:
            httpx_client.close()

    def test_httpx_client_without_auth_headers_fails(self):
        """
        Test that an httpx client WITHOUT auth headers fails to authenticate.

        This is a negative test to prove that authentication is actually required
        and that our positive tests are meaningful.
        """
        postgrest_base_url = f"{Config.SUPABASE_URL}/rest/v1"

        # Create httpx client WITHOUT auth headers (the bug we fixed)
        httpx_client = httpx.Client(
            base_url=postgrest_base_url,
            timeout=httpx.Timeout(30.0, connect=10.0),
            http2=True,
        )

        try:
            response = httpx_client.get("/users", params={"select": "*", "limit": "1"})

            # Should fail with 401 Unauthorized because no auth headers
            assert response.status_code in (401, 403), (
                f"Expected 401 or 403 without auth headers, got {response.status_code}. "
                "This suggests Supabase is not requiring authentication, which is unexpected."
            )

            # Verify the error message mentions missing API key
            response_json = response.json()
            assert (
                "api" in response_json.get("message", "").lower()
                or "key" in response_json.get("message", "").lower()
            ), f"Expected error about missing API key, got: {response_json}"
        finally:
            httpx_client.close()

    def test_httpx_client_with_only_apikey_header_succeeds(self):
        """
        Test that apikey header alone is sufficient for read operations.

        Supabase accepts apikey in the header for authentication.
        The Authorization header provides additional context but apikey alone should work.
        """
        postgrest_base_url = f"{Config.SUPABASE_URL}/rest/v1"

        httpx_client = httpx.Client(
            base_url=postgrest_base_url,
            headers={
                "apikey": Config.SUPABASE_KEY,
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
            http2=True,
        )

        try:
            response = httpx_client.get("/users", params={"select": "*", "limit": "1"})

            assert response.status_code == 200, (
                f"Expected 200 with apikey header, got {response.status_code}. "
                f"Response: {response.text}"
            )
        finally:
            httpx_client.close()

    def test_supabase_client_initialization_succeeds(self):
        """
        Test that the actual Supabase client can be initialized and query the database.

        This tests the full initialization path in get_supabase_client() including
        the custom httpx client injection.
        """
        # Import here to avoid initialization side effects during test collection
        # Reset the global client to force re-initialization
        import src.config.supabase_config as supabase_module
        from src.config.supabase_config import _supabase_client, get_supabase_client

        original_client = supabase_module._supabase_client
        supabase_module._supabase_client = None

        try:
            # This should initialize the client with our httpx configuration
            client = get_supabase_client()

            # Verify the client was created
            assert client is not None, "get_supabase_client() returned None"

            # Make a real query to verify authentication works
            result = client.table("users").select("*").limit(1).execute()

            # If we got here without an exception, auth is working
            assert result is not None, "Query returned None"
            assert hasattr(result, "data"), "Query result missing 'data' attribute"

        finally:
            # Restore the original client state
            supabase_module._supabase_client = original_client

    def test_postgrest_session_has_correct_headers(self):
        """
        Test that after initialization, the postgrest session has the correct auth headers.

        This directly verifies that the httpx client injection worked correctly.
        """
        import src.config.supabase_config as supabase_module
        from src.config.supabase_config import get_supabase_client

        # Reset and re-initialize
        original_client = supabase_module._supabase_client
        supabase_module._supabase_client = None

        try:
            client = get_supabase_client()

            # Check that the postgrest session has our custom httpx client
            if hasattr(client, "postgrest") and hasattr(client.postgrest, "session"):
                session = client.postgrest.session

                # Verify it's an httpx client
                assert isinstance(
                    session, httpx.Client
                ), f"Expected httpx.Client, got {type(session)}"

                # Verify auth headers are present
                headers = session.headers

                assert "apikey" in headers, (
                    "Missing 'apikey' header in postgrest session. "
                    "This will cause 'No API key found in request' errors."
                )

                assert (
                    headers["apikey"] == Config.SUPABASE_KEY
                ), "apikey header value doesn't match SUPABASE_KEY"

                assert (
                    "authorization" in headers or "Authorization" in headers
                ), "Missing 'Authorization' header in postgrest session"

                auth_header = headers.get("authorization") or headers.get("Authorization")
                assert (
                    auth_header == f"Bearer {Config.SUPABASE_KEY}"
                ), f"Authorization header value incorrect: {auth_header}"

        finally:
            supabase_module._supabase_client = original_client


class TestSupabaseConnectionHealth:
    """
    Health check tests for Supabase connection.

    These tests verify basic connectivity and can be used as smoke tests
    to detect authentication issues early in CI/CD.
    """

    def test_can_reach_supabase_health_endpoint(self):
        """
        Test basic connectivity to Supabase.
        """
        # The health endpoint doesn't require auth
        response = httpx.get(f"{Config.SUPABASE_URL}/rest/v1/", timeout=10.0)

        # We expect either 200 or a 4xx (auth required), but not 5xx
        assert (
            response.status_code < 500
        ), f"Supabase server error: {response.status_code} - {response.text}"

    def test_supabase_url_has_correct_format(self):
        """
        Test that SUPABASE_URL is correctly formatted.
        """
        url = Config.SUPABASE_URL

        assert url, "SUPABASE_URL is empty"
        assert url.startswith("https://") or url.startswith(
            "http://"
        ), f"SUPABASE_URL must start with http:// or https://, got: {url}"
        assert (
            "supabase" in url.lower() or "localhost" in url.lower()
        ), f"SUPABASE_URL doesn't look like a Supabase URL: {url}"
