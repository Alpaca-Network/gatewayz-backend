#!/usr/bin/env python3
"""
Tests for temporary email bot status assignment

When users sign up with temporary/disposable email addresses,
their subscription_status should be set to 'bot' instead of 'trial'.
"""

import pytest
from datetime import datetime, timezone, UTC
from unittest.mock import patch, MagicMock


# ==================================================
# IN-MEMORY SUPABASE STUB
# ==================================================

class _Result:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count

    def execute(self):
        return self


class _BaseQuery:
    def __init__(self, store, table):
        self.store = store
        self.table = table
        self._filters = []
        self._orders = []
        self._limit = None

    def eq(self, field, value):
        self._filters.append(("eq", field, value))
        return self

    def neq(self, field, value):
        self._filters.append(("neq", field, value))
        return self

    def order(self, field, desc=False):
        self._orders.append((field, desc))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _match(self, row):
        for op, f, v in self._filters:
            rv = row.get(f)
            if op == "eq" and rv != v:
                return False
            elif op == "neq" and rv == v:
                return False
        return True

    def execute(self):
        rows = self.store.tables.get(self.table, [])
        matched = [r for r in rows if self._match(r)]

        if self._orders:
            for field, desc in reversed(self._orders):
                matched.sort(key=lambda x: x.get(field, 0), reverse=desc)

        if self._limit:
            matched = matched[:self._limit]

        return _Result(matched, len(matched))


class _SelectQuery(_BaseQuery):
    pass


class _InsertQuery:
    def __init__(self, store, table, data):
        self.store = store
        self.table = table
        self.data = data

    def execute(self):
        if not isinstance(self.data, list):
            self.data = [self.data]

        if self.table not in self.store.tables:
            self.store.tables[self.table] = []

        for record in self.data:
            if 'id' not in record:
                existing_ids = [int(r.get('id', 0)) for r in self.store.tables[self.table]]
                record['id'] = str(max(existing_ids, default=0) + 1)

        self.store.tables[self.table].extend(self.data)
        return _Result(self.data)


class _UpdateQuery(_BaseQuery):
    def __init__(self, store, table, data):
        super().__init__(store, table)
        self.update_data = data

    def execute(self):
        rows = self.store.tables.get(self.table, [])
        updated = []

        for row in rows:
            if self._match(row):
                row.update(self.update_data)
                updated.append(row)

        return _Result(updated)


class _DeleteQuery(_BaseQuery):
    def execute(self):
        rows = self.store.tables.get(self.table, [])
        to_delete = [r for r in rows if self._match(r)]
        self.store.tables[self.table] = [r for r in rows if not self._match(r)]
        return _Result(to_delete)


class _Table:
    def __init__(self, store, name):
        self.store = store
        self.name = name

    def select(self, *fields):
        return _SelectQuery(self.store, self.name)

    def insert(self, data):
        return _InsertQuery(self.store, self.name, data)

    def update(self, data):
        return _UpdateQuery(self.store, self.name, data)

    def delete(self):
        return _DeleteQuery(self.store, self.name)


class SupabaseStub:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _Table(self, name)


# ==================================================
# FIXTURES
# ==================================================

@pytest.fixture
def sb():
    """Provide in-memory Supabase stub with cleanup"""
    stub = SupabaseStub()
    yield stub
    stub.tables.clear()


@pytest.fixture
def client(sb, monkeypatch):
    """FastAPI test client with mocked dependencies"""
    import src.config.supabase_config
    monkeypatch.setattr(src.config.supabase_config, "get_supabase_client", lambda: sb)

    import src.db.users as users_module
    import src.db.api_keys as api_keys_module
    import src.db.activity as activity_module

    def mock_get_user_by_privy_id(privy_id):
        result = sb.table('users').select('*').eq('privy_user_id', privy_id).execute()
        return result.data[0] if result.data else None

    def mock_get_user_by_username(username):
        result = sb.table('users').select('*').eq('username', username).execute()
        return result.data[0] if result.data else None

    def mock_create_enhanced_user(username, email, auth_method, privy_user_id=None, credits=5, subscription_status="trial"):
        trial_expires_at = datetime.now(UTC).isoformat()
        user_data = {
            'username': username,
            'email': email,
            'credits': credits,
            'privy_user_id': privy_user_id,
            'auth_method': auth_method.value if hasattr(auth_method, 'value') else str(auth_method),
            'created_at': datetime.now(UTC).isoformat(),
            'subscription_status': subscription_status,
            'trial_expires_at': trial_expires_at,
            'tier': 'basic',
        }
        result = sb.table('users').insert(user_data).execute()
        created_user = result.data[0]

        api_key = f"gw_live_{username}_test"
        api_key_data = {
            'user_id': created_user['id'],
            'api_key': api_key,
            'key_name': 'Primary API Key',
            'is_primary': True,
            'is_active': True,
            'environment_tag': 'production',
        }
        sb.table('api_keys_new').insert(api_key_data).execute()

        return {
            'user_id': created_user['id'],
            'username': username,
            'email': email,
            'credits': credits,
            'primary_api_key': api_key,
            'api_key': api_key,
            'subscription_status': subscription_status,
            'trial_expires_at': trial_expires_at,
            'tier': 'basic',
        }

    def mock_log_activity(*args, **kwargs):
        pass

    monkeypatch.setattr(users_module, "get_user_by_privy_id", mock_get_user_by_privy_id)
    monkeypatch.setattr(users_module, "create_enhanced_user", mock_create_enhanced_user)
    monkeypatch.setattr(users_module, "get_user_by_username", mock_get_user_by_username)
    monkeypatch.setattr(activity_module, "log_activity", mock_log_activity)

    import sys
    if 'src.routes.auth' in sys.modules:
        auth_module = sys.modules['src.routes.auth']
        if hasattr(auth_module, "users_module"):
            monkeypatch.setattr(auth_module.users_module, "get_user_by_privy_id", mock_get_user_by_privy_id)
            monkeypatch.setattr(auth_module.users_module, "create_enhanced_user", mock_create_enhanced_user)
            monkeypatch.setattr(auth_module.users_module, "get_user_by_username", mock_get_user_by_username)
        monkeypatch.setattr(auth_module, "log_activity", mock_log_activity)
        if hasattr(auth_module, "supabase_config"):
            monkeypatch.setattr(auth_module.supabase_config, "get_supabase_client", lambda: sb)

    import src.enhanced_notification_service as notif_module

    class MockNotificationService:
        def send_welcome_email(self, *args, **kwargs):
            return True
        def send_welcome_email_if_needed(self, *args, **kwargs):
            return True
        def send_password_reset_email(self, *args, **kwargs):
            return "reset_token_123"

    monkeypatch.setattr(notif_module, "enhanced_notification_service", MockNotificationService())

    from src.main import app
    from fastapi.testclient import TestClient

    return TestClient(app)


# ==================================================
# TESTS: Temporary Email Bot Status - Privy Auth
# ==================================================

def test_privy_auth_temp_email_sets_bot_status(client, sb):
    """Test that new users with temporary email get subscription_status='bot'"""
    # Use a known temporary email domain from the blocklist
    temp_email = "user@tempmail.com"

    request_data = {
        "user": {
            "id": "privy_temp_user_123",
            "created_at": 1705123456,
            "linked_accounts": [
                {
                    "type": "email",
                    "email": temp_email,
                    "verified_at": 1705123456
                }
            ],
            "mfa_methods": [],
            "has_accepted_terms": True,
            "is_guest": False
        },
        "token": "privy_token_temp",
        "email": temp_email,
        "is_new_user": True
    }

    response = client.post('/auth', json=request_data)

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert data['is_new_user'] is True
    assert data['subscription_status'] == 'bot'


def test_privy_auth_normal_email_sets_trial_status(client, sb):
    """Test that new users with normal email get subscription_status='trial'"""
    normal_email = "user@gmail.com"

    request_data = {
        "user": {
            "id": "privy_normal_user_123",
            "created_at": 1705123456,
            "linked_accounts": [
                {
                    "type": "email",
                    "email": normal_email,
                    "verified_at": 1705123456
                }
            ],
            "mfa_methods": [],
            "has_accepted_terms": True,
            "is_guest": False
        },
        "token": "privy_token_normal",
        "email": normal_email,
        "is_new_user": True
    }

    response = client.post('/auth', json=request_data)

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert data['is_new_user'] is True
    assert data['subscription_status'] == 'trial'


def test_privy_auth_10minutemail_sets_bot_status(client, sb):
    """Test that 10minutemail.com domain gets bot status"""
    temp_email = "user@10minutemail.com"

    request_data = {
        "user": {
            "id": "privy_10min_user_123",
            "created_at": 1705123456,
            "linked_accounts": [
                {
                    "type": "email",
                    "email": temp_email,
                    "verified_at": 1705123456
                }
            ],
            "mfa_methods": [],
            "has_accepted_terms": True,
            "is_guest": False
        },
        "token": "privy_token_10min",
        "email": temp_email,
        "is_new_user": True
    }

    response = client.post('/auth', json=request_data)

    assert response.status_code == 200
    data = response.json()
    assert data['subscription_status'] == 'bot'


def test_privy_auth_guerrillamail_sets_bot_status(client, sb):
    """Test that guerrillamail.com domain gets bot status"""
    temp_email = "user@guerrillamail.com"

    request_data = {
        "user": {
            "id": "privy_guerrilla_user_123",
            "created_at": 1705123456,
            "linked_accounts": [
                {
                    "type": "email",
                    "email": temp_email,
                    "verified_at": 1705123456
                }
            ],
            "mfa_methods": [],
            "has_accepted_terms": True,
            "is_guest": False
        },
        "token": "privy_token_guerrilla",
        "email": temp_email,
        "is_new_user": True
    }

    response = client.post('/auth', json=request_data)

    assert response.status_code == 200
    data = response.json()
    assert data['subscription_status'] == 'bot'


def test_privy_auth_mailinator_sets_bot_status(client, sb):
    """Test that mailinator.com domain gets bot status"""
    temp_email = "user@mailinator.com"

    request_data = {
        "user": {
            "id": "privy_mailinator_user_123",
            "created_at": 1705123456,
            "linked_accounts": [
                {
                    "type": "email",
                    "email": temp_email,
                    "verified_at": 1705123456
                }
            ],
            "mfa_methods": [],
            "has_accepted_terms": True,
            "is_guest": False
        },
        "token": "privy_token_mailinator",
        "email": temp_email,
        "is_new_user": True
    }

    response = client.post('/auth', json=request_data)

    assert response.status_code == 200
    data = response.json()
    assert data['subscription_status'] == 'bot'


def test_privy_auth_yopmail_sets_bot_status(client, sb):
    """Test that yopmail.com domain gets bot status"""
    temp_email = "user@yopmail.com"

    request_data = {
        "user": {
            "id": "privy_yopmail_user_123",
            "created_at": 1705123456,
            "linked_accounts": [
                {
                    "type": "email",
                    "email": temp_email,
                    "verified_at": 1705123456
                }
            ],
            "mfa_methods": [],
            "has_accepted_terms": True,
            "is_guest": False
        },
        "token": "privy_token_yopmail",
        "email": temp_email,
        "is_new_user": True
    }

    response = client.post('/auth', json=request_data)

    assert response.status_code == 200
    data = response.json()
    assert data['subscription_status'] == 'bot'


# ==================================================
# TESTS: Blocked Email Domains Still Rejected
# ==================================================

def test_privy_auth_blocked_domain_still_rejected(client, sb):
    """Test that blocked abuse domains are still rejected (not just marked as bot)"""
    # rccg-clf.org is in the blocked domains list
    blocked_email = "user@rccg-clf.org"

    request_data = {
        "user": {
            "id": "privy_blocked_user_123",
            "created_at": 1705123456,
            "linked_accounts": [
                {
                    "type": "email",
                    "email": blocked_email,
                    "verified_at": 1705123456
                }
            ],
            "mfa_methods": [],
            "has_accepted_terms": True,
            "is_guest": False
        },
        "token": "privy_token_blocked",
        "email": blocked_email,
        "is_new_user": True
    }

    response = client.post('/auth', json=request_data)

    # Blocked domains should still be rejected
    assert response.status_code == 400
    assert "not allowed" in response.json()['detail'].lower()


# ==================================================
# TESTS: Direct Registration with Temporary Email
# ==================================================

def test_register_temp_email_sets_bot_status(client, sb):
    """Test that direct registration with temporary email sets bot status"""
    temp_email = "newuser@tempmail.com"

    request_data = {
        "username": "tempmailuser",
        "email": temp_email,
        "auth_method": "email"
    }

    response = client.post('/auth/register', json=request_data)

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert data['subscription_status'] == 'bot'


def test_register_normal_email_sets_trial_status(client, sb):
    """Test that direct registration with normal email sets trial status"""
    normal_email = "newuser@company.com"

    request_data = {
        "username": "normaluser",
        "email": normal_email,
        "auth_method": "email"
    }

    response = client.post('/auth/register', json=request_data)

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert data['subscription_status'] == 'trial'


def test_register_blocked_domain_still_rejected(client, sb):
    """Test that blocked domains are still rejected in registration"""
    blocked_email = "user@rccg-clf.org"

    request_data = {
        "username": "blockeduser",
        "email": blocked_email,
        "auth_method": "email"
    }

    response = client.post('/auth/register', json=request_data)

    assert response.status_code == 400
    assert "not allowed" in response.json()['detail'].lower()


# ==================================================
# TESTS: Database Status Verification
# ==================================================

def test_temp_email_user_stored_with_bot_status_in_db(client, sb):
    """Verify that users with temp email are stored with bot status in database"""
    temp_email = "dbtest@tempmail.com"

    request_data = {
        "user": {
            "id": "privy_dbtest_123",
            "created_at": 1705123456,
            "linked_accounts": [
                {
                    "type": "email",
                    "email": temp_email,
                    "verified_at": 1705123456
                }
            ],
            "mfa_methods": [],
            "has_accepted_terms": True,
            "is_guest": False
        },
        "token": "privy_token_dbtest",
        "email": temp_email,
        "is_new_user": True
    }

    response = client.post('/auth', json=request_data)
    assert response.status_code == 200

    # Verify in the stub database
    users = sb.table('users').select('*').eq('email', temp_email).execute()
    assert len(users.data) == 1
    assert users.data[0]['subscription_status'] == 'bot'


def test_normal_email_user_stored_with_trial_status_in_db(client, sb):
    """Verify that users with normal email are stored with trial status in database"""
    normal_email = "normaldbtest@company.com"

    request_data = {
        "user": {
            "id": "privy_normaldbtest_123",
            "created_at": 1705123456,
            "linked_accounts": [
                {
                    "type": "email",
                    "email": normal_email,
                    "verified_at": 1705123456
                }
            ],
            "mfa_methods": [],
            "has_accepted_terms": True,
            "is_guest": False
        },
        "token": "privy_token_normaldbtest",
        "email": normal_email,
        "is_new_user": True
    }

    response = client.post('/auth', json=request_data)
    assert response.status_code == 200

    # Verify in the stub database
    users = sb.table('users').select('*').eq('email', normal_email).execute()
    assert len(users.data) == 1
    assert users.data[0]['subscription_status'] == 'trial'
