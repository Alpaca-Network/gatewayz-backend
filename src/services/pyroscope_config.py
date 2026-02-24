"""
Pyroscope continuous profiling configuration.

What this does
--------------
Pyroscope runs a background thread inside the FastAPI process that wakes up
every 10 ms, captures a snapshot of every active Python call stack, and
accumulates those snapshots into a flamegraph.  Every 15 seconds (by default)
the accumulated flamegraph is flushed and pushed over HTTP to the Grafana Cloud
hosted Pyroscope endpoint.

No extra Railway service is required.  The only network traffic is the
periodic HTTP push from the backend container to Grafana Cloud.

What you see in Grafana
-----------------------
1.  In the Pyroscope datasource:  a continuous flamegraph of the whole process,
    broken down by the tags applied at the time of sampling.  Tags are:
        endpoint    – normalised URL path  (e.g. /v1/chat/completions)
        method      – HTTP verb            (GET, POST, …)
        service_name – always "gatewayz-backend"
        environment  – Railway environment (production / staging / local)

2.  In Tempo's trace viewer:  every slow span has a "View Profile" button
    (enabled via tracesToProfiles in tempo.yml).  Clicking it opens Pyroscope
    filtered to service_name=gatewayz-backend and the exact time window of that
    span, showing which Python functions were actually running during that
    specific request.

Why endpoint tags matter
------------------------
The raw flamegraph for the whole process is useful, but it mixes every route
together.  The endpoint tag lets you ask:
  "Which functions account for the most CPU time specifically in
   /v1/chat/completions?"
vs
  "Which functions are slow in /admin/trial/analytics?"
...without rebuilding the service or adding manual instrumentation to routes.

Sampling vs Sentry profiling
-----------------------------
Sentry already has profiles_sample_rate=0.05 (5 % of transactions).
That profile only fires when Sentry samples a transaction, which means the rare
P99 slow request is very unlikely to be captured.
Pyroscope samples EVERY 10 ms regardless — it always catches outliers.

Environment variables
---------------------
PYROSCOPE_ENABLED           Set to "true" to activate.  Defaults to "false"
                             so local/test environments are never affected.
PYROSCOPE_SERVER_ADDRESS    Full push URL from Grafana Cloud → Connections →
                             Pyroscope.  Example:
                             https://profiles-prod-006.grafana.net
PYROSCOPE_AUTH_USER         Grafana Cloud numeric instance ID.
PYROSCOPE_AUTH_PASSWORD     Grafana Cloud API token (profiles:write scope).
"""

import logging
import os
from contextlib import nullcontext

logger = logging.getLogger(__name__)

_initialized: bool = False


def init_pyroscope() -> bool:
    """
    Configure and start the Pyroscope sampler.

    Called once during application startup, synchronously, so the sampler is
    running before the first request arrives.

    Returns True if profiling started successfully, False otherwise.
    Never raises — a profiling failure must not prevent the API from starting.
    """
    global _initialized

    if _initialized:
        return True

    if os.getenv("PYROSCOPE_ENABLED", "false").lower() != "true":
        logger.info(
            "Pyroscope profiling is disabled. "
            "Set PYROSCOPE_ENABLED=true and PYROSCOPE_SERVER_ADDRESS to enable."
        )
        return False

    server_address = os.getenv("PYROSCOPE_SERVER_ADDRESS", "").strip()
    if not server_address:
        logger.warning(
            "PYROSCOPE_ENABLED=true but PYROSCOPE_SERVER_ADDRESS is not set — "
            "profiling will not start."
        )
        return False

    try:
        import pyroscope  # noqa: PLC0415  (late import is intentional)

        auth_user = os.getenv("PYROSCOPE_AUTH_USER", "")
        auth_password = os.getenv("PYROSCOPE_AUTH_PASSWORD", "")
        environment = os.getenv("RAILWAY_ENVIRONMENT", "local")

        configure_kwargs: dict = {
            "application_name": "gatewayz-backend",
            "server_address": server_address,
            # Tags applied to every sample this process emits.
            # These appear as filter labels in the Grafana Pyroscope UI.
            "tags": {
                "service_name": "gatewayz-backend",
                "environment": environment,
            },
            # 100 Hz = one sample every 10 ms.  This is the pyroscope default
            # and is low enough that the overhead on a busy async server is
            # negligible (< 1 % CPU in practice).
            "sample_rate": 100,
        }

        if auth_user and auth_password:
            configure_kwargs["basic_auth_username"] = auth_user
            configure_kwargs["basic_auth_password"] = auth_password

        pyroscope.configure(**configure_kwargs)

        _initialized = True
        logger.info(
            "✅ Pyroscope profiling started — " "pushing flamegraphs to %s (env=%s, rate=100 Hz)",
            server_address,
            environment,
        )
        return True

    except ImportError:
        logger.warning(
            "pyroscope-io is not installed in this environment. "
            "Profiling is skipped.  Add pyroscope-io>=0.8.7 to requirements.txt."
        )
        return False

    except Exception as exc:
        logger.warning("Pyroscope initialisation warning: %s", exc)
        return False


def shutdown_pyroscope() -> None:
    """
    Flush any buffered profile data and stop the sampler.

    Called during graceful shutdown so that the final ~15 s of profiling data
    are not lost when the container exits.
    """
    global _initialized

    if not _initialized:
        return

    try:
        import pyroscope  # noqa: PLC0415

        pyroscope.shutdown()
        logger.info("Pyroscope: final profile flush complete.")
    except Exception as exc:
        logger.warning("Pyroscope shutdown warning: %s", exc)
    finally:
        _initialized = False


def tag_wrapper(tags: dict):
    """
    Return a pyroscope tag_wrapper context manager.

    Usage in middleware::

        with pyroscope_config.tag_wrapper({"endpoint": "/v1/chat/completions"}):
            await self.app(scope, receive, send)

    Every call-stack sample taken while the block is executing will be labelled
    with the provided tags.  If pyroscope is not installed or not initialised,
    this returns a no-op nullcontext() so the calling code is unchanged.

    Why this matters
    ----------------
    Without tags, the flamegraph is a single flat view of the whole process.
    With the ``endpoint`` tag you can filter in Grafana to see:

        "Show me only the call stacks sampled during /v1/chat/completions
         requests — which Python function consumes the most CPU there?"

    For streaming LLM responses where the request may hold the event-loop for
    20–30 seconds, this reveals exactly which part of the code is running
    (token chunking?  Redis rate-limit check?  httpx send?).
    """
    if not _initialized:
        return nullcontext()

    try:
        import pyroscope  # noqa: PLC0415

        return pyroscope.tag_wrapper(tags)
    except Exception:
        return nullcontext()
