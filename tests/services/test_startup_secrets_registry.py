"""Startup hook for secret fingerprint recording (Phase D, D1).

src/services/startup.py's lifespan() calls
secrets_registry.record_secret_fingerprints() once at startup so
GET /admin/status.secrets has fresh rotation-age data. See
tests/services/test_secrets_registry.py for the recording logic itself and
tests/services/test_startup_upstream_pseudonym_guard.py for the AST-based
call-site convention this test mirrors.
"""

import ast
from pathlib import Path


def test_lifespan_source_calls_record_secret_fingerprints():
    startup_py = Path(__file__).resolve().parents[2] / "src" / "services" / "startup.py"
    tree = ast.parse(startup_py.read_text())
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "record_secret_fingerprints"
    ]
    assert calls, "record_secret_fingerprints must be called from lifespan()"


def test_lifespan_imports_record_secret_fingerprints_from_secrets_registry():
    startup_py = Path(__file__).resolve().parents[2] / "src" / "services" / "startup.py"
    tree = ast.parse(startup_py.read_text())
    imports = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        and n.module == "src.services.secrets_registry"
        and any(alias.name == "record_secret_fingerprints" for alias in n.names)
    ]
    assert imports, "startup.py must import record_secret_fingerprints from secrets_registry"
