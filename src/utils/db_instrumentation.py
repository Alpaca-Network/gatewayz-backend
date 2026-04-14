import time
from contextlib import contextmanager


@contextmanager
def track_database_query(table: str, operation: str, query_description: str | None = None):
    """
    Context manager to track database query metrics with both Prometheus and Sentry.

    This creates:
    - Prometheus metrics for database query count and duration
    - Sentry spans for Queries Insights (https://docs.sentry.io/product/insights/backend/queries/)

    Args:
        table: Database table name
        operation: Operation type (select, insert, update, delete)
        query_description: Optional parameterized query string for Sentry Insights
    """
    from src.services.prometheus_metrics import database_query_count, database_query_duration

    # Import Sentry insights utilities (isolated try/except to avoid double yield)
    trace_supabase_query = None
    try:
        from src.utils.sentry_insights import trace_supabase_query as _trace_supabase_query

        trace_supabase_query = _trace_supabase_query
    except ImportError:
        pass  # Sentry insights not available

    start_time = time.time()

    if trace_supabase_query:
        # Build query description if not provided
        query_desc = query_description or f"{operation.upper()} FROM {table}"

        with trace_supabase_query(table, operation, query_description=query_desc):
            try:
                yield
            finally:
                duration = time.time() - start_time
                database_query_count.labels(table=table, operation=operation).inc()
                database_query_duration.labels(table=table).observe(duration)
    else:
        # Sentry insights not available, fall back to Prometheus only
        try:
            yield
        finally:
            duration = time.time() - start_time
            database_query_count.labels(table=table, operation=operation).inc()
            database_query_duration.labels(table=table).observe(duration)
