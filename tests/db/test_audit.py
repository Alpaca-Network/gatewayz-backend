"""Tests for src/db/audit.py (Phase A3 unified audit_log)."""

from unittest.mock import MagicMock, patch

from src.db.audit import list_audit, record_audit


class TestRecordAudit:
    def test_records_api_key_actor(self):
        client = MagicMock()
        actor = {"id": 7, "email": "admin@example.com", "role": "admin"}

        with patch("src.db.audit.get_supabase_client", return_value=client):
            record_audit(
                actor,
                action="staff.role_changed",
                target_type="user",
                target_id=42,
                metadata={"new_role": "admin"},
            )

        client.table.assert_called_with("audit_log")
        inserted = client.table.return_value.insert.call_args[0][0]
        assert inserted["actor_user_id"] == 7
        assert inserted["actor_email"] == "admin@example.com"
        assert inserted["actor_auth"] == "api_key"
        assert inserted["action"] == "staff.role_changed"
        assert inserted["target_type"] == "user"
        assert inserted["target_id"] == "42"
        assert inserted["metadata"] == {"new_role": "admin"}

    def test_records_env_key_actor(self):
        client = MagicMock()
        actor = {"role": "admin", "auth": "env_key", "is_admin": True}

        with patch("src.db.audit.get_supabase_client", return_value=client):
            record_audit(
                actor, action="gpu.provider_approved", target_type="gpu_provider", target_id=1
            )

        inserted = client.table.return_value.insert.call_args[0][0]
        assert inserted["actor_auth"] == "env_key"
        assert inserted["actor_user_id"] is None

    def test_records_system_actor_when_none(self):
        client = MagicMock()

        with patch("src.db.audit.get_supabase_client", return_value=client):
            record_audit(None, action="system.something")

        inserted = client.table.return_value.insert.call_args[0][0]
        assert inserted["actor_auth"] == "system"

    def test_never_raises_on_db_failure(self):
        client = MagicMock()
        client.table.side_effect = RuntimeError("db down")

        with patch("src.db.audit.get_supabase_client", return_value=client):
            # Must not raise.
            record_audit({"id": 1}, action="anything")

    def test_extracts_ip_from_x_forwarded_for(self):
        client = MagicMock()
        request = MagicMock()
        request.headers.get.side_effect = lambda k, default=None: {
            "X-Forwarded-For": "1.2.3.4, 5.6.7.8",
            "user-agent": "pytest-agent",
        }.get(k, default)

        with patch("src.db.audit.get_supabase_client", return_value=client):
            record_audit({"id": 1}, action="a", request=request)

        inserted = client.table.return_value.insert.call_args[0][0]
        assert inserted["ip"] == "1.2.3.4"
        assert inserted["user_agent"] == "pytest-agent"

    def test_falls_back_to_direct_client_ip(self):
        client = MagicMock()
        request = MagicMock()
        request.headers.get.side_effect = lambda k, default=None: default
        request.client.host = "9.9.9.9"

        with patch("src.db.audit.get_supabase_client", return_value=client):
            record_audit({"id": 1}, action="a", request=request)

        inserted = client.table.return_value.insert.call_args[0][0]
        assert inserted["ip"] == "9.9.9.9"


class TestListAudit:
    def test_returns_rows(self):
        client = MagicMock()
        rows = [{"id": 1, "action": "staff.role_changed"}]
        client.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value.data = (
            rows
        )

        with patch("src.db.audit.get_supabase_client", return_value=client):
            result = list_audit(limit=10)

        assert result == rows

    def test_returns_empty_list_on_error(self):
        client = MagicMock()
        client.table.side_effect = RuntimeError("db down")

        with patch("src.db.audit.get_supabase_client", return_value=client):
            result = list_audit()

        assert result == []

    def test_filters_by_action(self):
        client = MagicMock()
        query = client.table.return_value.select.return_value
        query.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []

        with patch("src.db.audit.get_supabase_client", return_value=client):
            list_audit(action="staff.role_changed")

        query.eq.assert_any_call("action", "staff.role_changed")

    def test_limit_is_capped(self):
        client = MagicMock()
        query = client.table.return_value.select.return_value
        query.order.return_value.limit.return_value.execute.return_value.data = []

        with patch("src.db.audit.get_supabase_client", return_value=client):
            list_audit(limit=10000)

        query.order.return_value.limit.assert_called_with(500)
