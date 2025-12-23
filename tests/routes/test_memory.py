#!/usr/bin/env python3
"""
Comprehensive tests for memory API endpoints

Tests cover:
- Memory listing endpoint with filtering
- Memory statistics endpoint
- Get single memory endpoint
- Delete memory endpoint
- Delete all memories endpoint
- Memory extraction endpoint
- Categories endpoint
- User authentication
- Error handling
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import get_api_key


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture
def client():
    """FastAPI test client"""
    # Clear any existing dependency overrides
    app.dependency_overrides = {}
    yield TestClient(app)
    # Cleanup after test
    app.dependency_overrides = {}


@pytest.fixture
def mock_api_key():
    """Mock API key"""
    return "test-api-key-123"


@pytest.fixture
def mock_user():
    """Mock authenticated user"""
    return {
        'id': 123,
        'username': 'testuser',
        'email': 'test@example.com',
        'role': 'user'
    }


@pytest.fixture
def mock_memories():
    """Mock memory entries"""
    return [
        {
            'id': 1,
            'user_id': 123,
            'category': 'preference',
            'content': 'User prefers Python over JavaScript',
            'source_session_id': 100,
            'confidence': 0.85,
            'is_active': True,
            'access_count': 3,
            'last_accessed_at': '2024-01-15T10:00:00Z',
            'created_at': '2024-01-10T08:00:00Z',
            'updated_at': '2024-01-15T10:00:00Z'
        },
        {
            'id': 2,
            'user_id': 123,
            'category': 'context',
            'content': 'User is a backend engineer at a startup',
            'source_session_id': 101,
            'confidence': 0.90,
            'is_active': True,
            'access_count': 5,
            'last_accessed_at': '2024-01-16T14:00:00Z',
            'created_at': '2024-01-11T09:00:00Z',
            'updated_at': '2024-01-16T14:00:00Z'
        },
        {
            'id': 3,
            'user_id': 123,
            'category': 'fact',
            'content': "User's name is Alex",
            'source_session_id': 102,
            'confidence': 0.95,
            'is_active': True,
            'access_count': 10,
            'last_accessed_at': '2024-01-17T12:00:00Z',
            'created_at': '2024-01-12T10:00:00Z',
            'updated_at': '2024-01-17T12:00:00Z'
        }
    ]


@pytest.fixture
def mock_memory_stats():
    """Mock memory statistics"""
    return {
        'total_memories': 15,
        'by_category': {
            'preference': 5,
            'context': 3,
            'fact': 4,
            'name': 2,
            'project': 1
        },
        'oldest_memory': '2024-01-01T00:00:00Z',
        'newest_memory': '2024-01-17T12:00:00Z'
    }


@pytest.fixture
def mock_chat_session():
    """Mock chat session with messages"""
    return {
        'id': 100,
        'user_id': 123,
        'title': 'Test Session',
        'messages': [
            {'role': 'user', 'content': 'Hi, I prefer Python for backend development'},
            {'role': 'assistant', 'content': 'Great! Python is excellent for backend development.'},
            {'role': 'user', 'content': 'Yes, I work as a senior engineer at a startup.'},
            {'role': 'assistant', 'content': 'That sounds interesting! How can I help you today?'}
        ]
    }


# ============================================================
# TEST CLASS: List Memories Endpoint
# ============================================================

class TestListMemoriesEndpoint:
    """Test GET /v1/memory endpoint"""

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_user_memories')
    def test_list_memories_default(
        self,
        mock_get_memories,
        mock_get_user,
        client,
        mock_api_key,
        mock_user,
        mock_memories
    ):
        """Test listing memories with default parameters"""
        # Override dependency
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_memories.return_value = mock_memories

        response = client.get('/v1/memory')

        assert response.status_code == 200
        data = response.json()

        assert data['success'] is True
        assert data['count'] == 3
        assert len(data['data']) == 3
        assert data['data'][0]['category'] == 'preference'

        # Verify get_user_memories was called with defaults
        mock_get_memories.assert_called_once_with(
            user_id=123,
            category=None,
            limit=50,
            offset=0,
            active_only=True
        )

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_user_memories')
    def test_list_memories_with_category(
        self,
        mock_get_memories,
        mock_get_user,
        client,
        mock_api_key,
        mock_user,
        mock_memories
    ):
        """Test listing memories filtered by category"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_memories.return_value = [mock_memories[0]]  # Only preference

        response = client.get('/v1/memory?category=preference')

        assert response.status_code == 200
        data = response.json()

        assert data['count'] == 1
        assert data['data'][0]['category'] == 'preference'

        mock_get_memories.assert_called_once_with(
            user_id=123,
            category='preference',
            limit=50,
            offset=0,
            active_only=True
        )

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_user_memories')
    def test_list_memories_with_pagination(
        self,
        mock_get_memories,
        mock_get_user,
        client,
        mock_api_key,
        mock_user,
        mock_memories
    ):
        """Test listing memories with pagination"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_memories.return_value = mock_memories

        response = client.get('/v1/memory?limit=10&offset=20')

        assert response.status_code == 200

        mock_get_memories.assert_called_once_with(
            user_id=123,
            category=None,
            limit=10,
            offset=20,
            active_only=True
        )

    @patch('src.routes.memory.get_user')
    def test_list_memories_invalid_category(
        self,
        mock_get_user,
        client,
        mock_api_key,
        mock_user
    ):
        """Test listing memories with invalid category"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user

        response = client.get('/v1/memory?category=invalid_category')

        assert response.status_code == 400
        assert 'Invalid category' in response.json()['detail']

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_user_memories')
    def test_list_memories_empty(
        self,
        mock_get_memories,
        mock_get_user,
        client,
        mock_api_key,
        mock_user
    ):
        """Test listing memories when none exist"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_memories.return_value = []

        response = client.get('/v1/memory')

        assert response.status_code == 200
        data = response.json()

        assert data['success'] is True
        assert data['count'] == 0
        assert data['data'] == []

    def test_list_memories_limit_validation(self, client, mock_api_key):
        """Test validation for limit parameter"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key

        # Limit must be >= 1
        response = client.get('/v1/memory?limit=0')
        assert response.status_code == 422

        # Limit must be <= 100
        response = client.get('/v1/memory?limit=101')
        assert response.status_code == 422

    def test_list_memories_offset_validation(self, client, mock_api_key):
        """Test validation for offset parameter"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key

        # Offset must be >= 0
        response = client.get('/v1/memory?offset=-1')
        assert response.status_code == 422


# ============================================================
# TEST CLASS: Get Memory Statistics Endpoint
# ============================================================

class TestMemoryStatsEndpoint:
    """Test GET /v1/memory/stats endpoint"""

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_user_memory_stats')
    def test_get_stats_success(
        self,
        mock_get_stats,
        mock_get_user,
        client,
        mock_api_key,
        mock_user,
        mock_memory_stats
    ):
        """Test getting memory statistics"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_stats.return_value = mock_memory_stats

        response = client.get('/v1/memory/stats')

        assert response.status_code == 200
        data = response.json()

        assert data['success'] is True
        assert data['stats']['total_memories'] == 15
        assert data['stats']['by_category']['preference'] == 5
        assert 'oldest_memory' in data['stats']
        assert 'newest_memory' in data['stats']

        mock_get_stats.assert_called_once_with(123)

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_user_memory_stats')
    def test_get_stats_empty(
        self,
        mock_get_stats,
        mock_get_user,
        client,
        mock_api_key,
        mock_user
    ):
        """Test getting stats when no memories exist"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_stats.return_value = {
            'total_memories': 0,
            'by_category': {},
            'oldest_memory': None,
            'newest_memory': None
        }

        response = client.get('/v1/memory/stats')

        assert response.status_code == 200
        data = response.json()

        assert data['success'] is True
        assert data['stats']['total_memories'] == 0

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_user_memory_stats')
    def test_get_stats_error(
        self,
        mock_get_stats,
        mock_get_user,
        client,
        mock_api_key,
        mock_user
    ):
        """Test error handling in stats endpoint"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_stats.side_effect = Exception("Database error")

        response = client.get('/v1/memory/stats')

        assert response.status_code == 500
        assert 'Failed to get memory stats' in response.json()['detail']


# ============================================================
# TEST CLASS: Get Single Memory Endpoint
# ============================================================

class TestGetMemoryEndpoint:
    """Test GET /v1/memory/{memory_id} endpoint"""

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_memory_by_id')
    def test_get_memory_success(
        self,
        mock_get_memory,
        mock_get_user,
        client,
        mock_api_key,
        mock_user,
        mock_memories
    ):
        """Test getting a specific memory"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_memory.return_value = mock_memories[0]

        response = client.get('/v1/memory/1')

        assert response.status_code == 200
        data = response.json()

        assert data['success'] is True
        assert data['data']['id'] == 1
        assert data['data']['category'] == 'preference'

        mock_get_memory.assert_called_once_with(1, 123)

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_memory_by_id')
    def test_get_memory_not_found(
        self,
        mock_get_memory,
        mock_get_user,
        client,
        mock_api_key,
        mock_user
    ):
        """Test getting a non-existent memory"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_memory.return_value = None

        response = client.get('/v1/memory/999')

        assert response.status_code == 404
        assert 'Memory not found' in response.json()['detail']

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_memory_by_id')
    def test_get_memory_error(
        self,
        mock_get_memory,
        mock_get_user,
        client,
        mock_api_key,
        mock_user
    ):
        """Test error handling when getting memory"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_memory.side_effect = Exception("Database error")

        response = client.get('/v1/memory/1')

        assert response.status_code == 500
        assert 'Failed to get memory' in response.json()['detail']


# ============================================================
# TEST CLASS: Delete Memory Endpoint
# ============================================================

class TestDeleteMemoryEndpoint:
    """Test DELETE /v1/memory/{memory_id} endpoint"""

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_memory_by_id')
    @patch('src.routes.memory.delete_user_memory')
    def test_delete_memory_soft(
        self,
        mock_delete,
        mock_get_memory,
        mock_get_user,
        client,
        mock_api_key,
        mock_user,
        mock_memories
    ):
        """Test soft deleting a memory"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_memory.return_value = mock_memories[0]
        mock_delete.return_value = True

        response = client.delete('/v1/memory/1')

        assert response.status_code == 200
        data = response.json()

        assert data['success'] is True
        assert data['deleted_count'] == 1

        mock_delete.assert_called_once_with(1, 123)

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_memory_by_id')
    @patch('src.routes.memory.hard_delete_user_memory')
    def test_delete_memory_permanent(
        self,
        mock_hard_delete,
        mock_get_memory,
        mock_get_user,
        client,
        mock_api_key,
        mock_user,
        mock_memories
    ):
        """Test permanently deleting a memory"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_memory.return_value = mock_memories[0]
        mock_hard_delete.return_value = True

        response = client.delete('/v1/memory/1?permanent=true')

        assert response.status_code == 200
        data = response.json()

        assert data['success'] is True
        assert data['deleted_count'] == 1

        mock_hard_delete.assert_called_once_with(1, 123)

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_memory_by_id')
    def test_delete_memory_not_found(
        self,
        mock_get_memory,
        mock_get_user,
        client,
        mock_api_key,
        mock_user
    ):
        """Test deleting a non-existent memory"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_memory.return_value = None

        response = client.delete('/v1/memory/999')

        assert response.status_code == 404
        assert 'Memory not found' in response.json()['detail']

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_memory_by_id')
    @patch('src.routes.memory.delete_user_memory')
    def test_delete_memory_failure(
        self,
        mock_delete,
        mock_get_memory,
        mock_get_user,
        client,
        mock_api_key,
        mock_user,
        mock_memories
    ):
        """Test handling deletion failure"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_memory.return_value = mock_memories[0]
        mock_delete.return_value = False

        response = client.delete('/v1/memory/1')

        assert response.status_code == 500
        assert 'Failed to delete memory' in response.json()['detail']


# ============================================================
# TEST CLASS: Delete All Memories Endpoint
# ============================================================

class TestDeleteAllMemoriesEndpoint:
    """Test DELETE /v1/memory endpoint"""

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.delete_all_user_memories')
    def test_delete_all_memories_soft(
        self,
        mock_delete_all,
        mock_get_user,
        client,
        mock_api_key,
        mock_user
    ):
        """Test soft deleting all memories"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_delete_all.return_value = 15

        response = client.delete('/v1/memory')

        assert response.status_code == 200
        data = response.json()

        assert data['success'] is True
        assert data['deleted_count'] == 15

        mock_delete_all.assert_called_once_with(123, hard_delete=False)

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.delete_all_user_memories')
    def test_delete_all_memories_permanent(
        self,
        mock_delete_all,
        mock_get_user,
        client,
        mock_api_key,
        mock_user
    ):
        """Test permanently deleting all memories"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_delete_all.return_value = 15

        response = client.delete('/v1/memory?permanent=true')

        assert response.status_code == 200
        data = response.json()

        assert data['success'] is True
        assert data['deleted_count'] == 15

        mock_delete_all.assert_called_once_with(123, hard_delete=True)

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.delete_all_user_memories')
    def test_delete_all_memories_none_exist(
        self,
        mock_delete_all,
        mock_get_user,
        client,
        mock_api_key,
        mock_user
    ):
        """Test deleting when no memories exist"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_delete_all.return_value = 0

        response = client.delete('/v1/memory')

        assert response.status_code == 200
        data = response.json()

        assert data['success'] is True
        assert data['deleted_count'] == 0

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.delete_all_user_memories')
    def test_delete_all_memories_error(
        self,
        mock_delete_all,
        mock_get_user,
        client,
        mock_api_key,
        mock_user
    ):
        """Test error handling when deleting all"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_delete_all.side_effect = Exception("Database error")

        response = client.delete('/v1/memory')

        assert response.status_code == 500
        assert 'Failed to delete all memories' in response.json()['detail']


# ============================================================
# TEST CLASS: Extract Memories Endpoint
# ============================================================

class TestExtractMemoriesEndpoint:
    """Test POST /v1/memory/extract endpoint"""

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_chat_session')
    @patch('src.routes.memory.memory_service.extract_memories_from_messages')
    def test_extract_memories_success(
        self,
        mock_extract,
        mock_get_session,
        mock_get_user,
        client,
        mock_api_key,
        mock_user,
        mock_chat_session,
        mock_memories
    ):
        """Test extracting memories from a session"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_session.return_value = mock_chat_session
        mock_extract.return_value = mock_memories[:2]

        response = client.post(
            '/v1/memory/extract',
            json={'session_id': 100}
        )

        assert response.status_code == 200
        data = response.json()

        assert data['success'] is True
        assert data['extracted_count'] == 2
        assert len(data['memories']) == 2

        mock_get_session.assert_called_once_with(100, 123)

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_chat_session')
    def test_extract_memories_session_not_found(
        self,
        mock_get_session,
        mock_get_user,
        client,
        mock_api_key,
        mock_user
    ):
        """Test extraction with non-existent session"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_session.return_value = None

        response = client.post(
            '/v1/memory/extract',
            json={'session_id': 999}
        )

        assert response.status_code == 404
        assert 'Chat session not found' in response.json()['detail']

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_chat_session')
    def test_extract_memories_empty_session(
        self,
        mock_get_session,
        mock_get_user,
        client,
        mock_api_key,
        mock_user
    ):
        """Test extraction from session with no messages"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_session.return_value = {
            'id': 100,
            'user_id': 123,
            'messages': []
        }

        response = client.post(
            '/v1/memory/extract',
            json={'session_id': 100}
        )

        assert response.status_code == 200
        data = response.json()

        assert data['success'] is True
        assert data['extracted_count'] == 0
        assert 'No messages in session' in data['message']

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_chat_session')
    @patch('src.routes.memory.memory_service.extract_memories_from_messages')
    def test_extract_memories_error(
        self,
        mock_extract,
        mock_get_session,
        mock_get_user,
        client,
        mock_api_key,
        mock_user,
        mock_chat_session
    ):
        """Test error handling during extraction"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_session.return_value = mock_chat_session
        mock_extract.side_effect = Exception("LLM API error")

        response = client.post(
            '/v1/memory/extract',
            json={'session_id': 100}
        )

        assert response.status_code == 500
        assert 'Failed to extract memories' in response.json()['detail']


# ============================================================
# TEST CLASS: Categories Endpoint
# ============================================================

class TestCategoriesEndpoint:
    """Test GET /v1/memory/categories endpoint"""

    def test_list_categories(self, client):
        """Test listing available categories"""
        response = client.get('/v1/memory/categories')

        assert response.status_code == 200
        data = response.json()

        assert 'categories' in data
        assert 'descriptions' in data
        assert 'preference' in data['categories']
        assert 'context' in data['categories']
        assert 'fact' in data['categories']
        assert 'name' in data['categories']
        assert 'project' in data['categories']
        assert 'general' in data['categories']

        # Check descriptions
        assert 'preference' in data['descriptions']
        assert isinstance(data['descriptions']['preference'], str)


# ============================================================
# TEST CLASS: Authentication
# ============================================================

class TestMemoryAuthentication:
    """Test authentication requirements"""

    @patch('src.routes.memory.get_user')
    def test_list_memories_invalid_api_key(
        self,
        mock_get_user,
        client,
        mock_api_key
    ):
        """Test that list endpoint requires valid API key"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = None  # Invalid API key

        response = client.get('/v1/memory')

        assert response.status_code == 401
        assert 'Invalid API key' in response.json()['detail']

    @patch('src.routes.memory.get_user')
    def test_stats_invalid_api_key(
        self,
        mock_get_user,
        client,
        mock_api_key
    ):
        """Test that stats endpoint requires valid API key"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = None

        response = client.get('/v1/memory/stats')

        assert response.status_code == 401
        assert 'Invalid API key' in response.json()['detail']

    @patch('src.routes.memory.get_user')
    def test_get_memory_invalid_api_key(
        self,
        mock_get_user,
        client,
        mock_api_key
    ):
        """Test that get memory endpoint requires valid API key"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = None

        response = client.get('/v1/memory/1')

        assert response.status_code == 401
        assert 'Invalid API key' in response.json()['detail']

    @patch('src.routes.memory.get_user')
    def test_delete_memory_invalid_api_key(
        self,
        mock_get_user,
        client,
        mock_api_key
    ):
        """Test that delete memory endpoint requires valid API key"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = None

        response = client.delete('/v1/memory/1')

        assert response.status_code == 401
        assert 'Invalid API key' in response.json()['detail']

    @patch('src.routes.memory.get_user')
    def test_delete_all_invalid_api_key(
        self,
        mock_get_user,
        client,
        mock_api_key
    ):
        """Test that delete all endpoint requires valid API key"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = None

        response = client.delete('/v1/memory')

        assert response.status_code == 401
        assert 'Invalid API key' in response.json()['detail']

    @patch('src.routes.memory.get_user')
    def test_extract_invalid_api_key(
        self,
        mock_get_user,
        client,
        mock_api_key
    ):
        """Test that extract endpoint requires valid API key"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = None

        response = client.post(
            '/v1/memory/extract',
            json={'session_id': 100}
        )

        assert response.status_code == 401
        assert 'Invalid API key' in response.json()['detail']


# ============================================================
# TEST CLASS: Integration Tests
# ============================================================

class TestMemoryIntegration:
    """Test memory endpoint integration scenarios"""

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_user_memories')
    @patch('src.routes.memory.get_user_memory_stats')
    def test_stats_and_list_consistency(
        self,
        mock_get_stats,
        mock_get_memories,
        mock_get_user,
        client,
        mock_api_key,
        mock_user,
        mock_memories,
        mock_memory_stats
    ):
        """Test that stats and list endpoints return consistent data"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_memories.return_value = mock_memories
        mock_get_stats.return_value = mock_memory_stats

        # Get stats
        stats_response = client.get('/v1/memory/stats')
        assert stats_response.status_code == 200

        # Get list
        list_response = client.get('/v1/memory')
        assert list_response.status_code == 200

        # Both should be for the same user
        assert mock_get_stats.call_args[0][0] == 123
        assert mock_get_memories.call_args[1]['user_id'] == 123

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_user_memories')
    def test_category_filtering_workflow(
        self,
        mock_get_memories,
        mock_get_user,
        client,
        mock_api_key,
        mock_user,
        mock_memories
    ):
        """Test filtering by different categories"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_memories.return_value = mock_memories

        # Test all valid categories
        categories = ['preference', 'context', 'instruction', 'fact', 'name', 'project', 'general']

        for category in categories:
            response = client.get(f'/v1/memory?category={category}')
            assert response.status_code == 200

            # Verify category was passed
            call_args = mock_get_memories.call_args[1]
            assert call_args['category'] == category

    @patch('src.routes.memory.get_user')
    @patch('src.routes.memory.get_memory_by_id')
    @patch('src.routes.memory.delete_user_memory')
    @patch('src.routes.memory.get_user_memories')
    def test_delete_and_verify_workflow(
        self,
        mock_get_memories,
        mock_delete,
        mock_get_memory,
        mock_get_user,
        client,
        mock_api_key,
        mock_user,
        mock_memories
    ):
        """Test delete then verify memory is gone workflow"""
        app.dependency_overrides[get_api_key] = lambda: mock_api_key
        mock_get_user.return_value = mock_user
        mock_get_memory.return_value = mock_memories[0]
        mock_delete.return_value = True

        # Delete memory
        delete_response = client.delete('/v1/memory/1')
        assert delete_response.status_code == 200

        # Verify deleted
        mock_get_memories.return_value = [m for m in mock_memories if m['id'] != 1]
        list_response = client.get('/v1/memory')
        assert list_response.status_code == 200
        assert list_response.json()['count'] == 2
