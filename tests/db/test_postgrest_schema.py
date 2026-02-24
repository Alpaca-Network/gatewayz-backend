import importlib

import pytest

MODULE_PATH = "src.db.postgrest_schema"


@pytest.fixture
def fake_supabase():
    """
    Placeholder fixture so the global skip_if_no_database autouse hook
    recognizes this test module as using the in-memory stub instead of the
    real Supabase connection.
    """
    return object()


def reload_module():
    module = importlib.import_module(MODULE_PATH)
    return importlib.reload(module)


def test_refresh_postgrest_schema_cache_falls_back_to_direct_notify(monkeypatch, fake_supabase):
    mod = reload_module()

    class FakeRPC:
        def execute(self):
            raise RuntimeError("{'code': 'PGRST202', 'message': 'function missing'}")

    class FakeClient:
        def rpc(self, name, params):
            assert name == "refresh_postgrest_schema_cache"
            assert params == {}
            return FakeRPC()

    monkeypatch.setattr(mod, "get_supabase_client", lambda: FakeClient())

    calls = {}

    class DummyCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql):
            calls["sql"] = sql
            calls["notified"] = calls.get("notified", 0) + 1

    class DummyConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return DummyCursor()

    class DummyPsycopg:
        def connect(self, dsn, autocommit):
            calls["dsn"] = dsn
            calls["autocommit"] = autocommit
            return DummyConn()

    monkeypatch.setattr(mod, "psycopg", DummyPsycopg())
    monkeypatch.setattr(mod.Config, "SUPABASE_DB_DSN", "postgresql://example/test")

    assert mod.refresh_postgrest_schema_cache() is True
    assert calls["notified"] == 1
    assert calls["sql"] == "NOTIFY pgrst, 'reload schema';"
    assert calls["autocommit"] is True
    assert calls["dsn"] == "postgresql://example/test"


def test_refresh_postgrest_schema_cache_returns_false_without_dsn(monkeypatch, fake_supabase):
    mod = reload_module()

    class FakeRPC:
        def execute(self):
            raise RuntimeError("{'code': 'PGRST202', 'message': 'function missing'}")

    class FakeClient:
        def rpc(self, name, params):
            return FakeRPC()

    monkeypatch.setattr(mod, "get_supabase_client", lambda: FakeClient())
    monkeypatch.setattr(mod.Config, "SUPABASE_DB_DSN", None)

    assert mod.refresh_postgrest_schema_cache() is False
