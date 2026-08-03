"""Reset the monitor's module-level scope cache around every test here.

_get_servable_model_ids memoises its result in a module global for 5 minutes.
Under xdist several test files share a worker process, so one test populating
that cache silently changed which models a later test in a *different* file saw
as probeable — it turned an unrelated assertion red. Global mutable state needs
an explicit boundary.
"""

import pytest

from src.services.monitoring import intelligent_health_monitor as ihm


@pytest.fixture(autouse=True)
def _reset_servable_scope():
    ihm._servable_ids = set()
    ihm._servable_loaded_at = 0.0
    yield
    ihm._servable_ids = set()
    ihm._servable_loaded_at = 0.0
