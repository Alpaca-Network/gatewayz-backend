"""admit_job_request / release_job_request against the real migrated schema (CI-only).

Migration 20261028130000. Proves the cap and the in-flight bound are enforced
atomically: a concurrent burst gets exactly max_inflight leases, never more.
"""

from __future__ import annotations

import secrets
import threading

import pytest

from tests.sql.test_job_usage_v2_sql import _insert_user


def _job(cur, cap=10, spent=0, status="running") -> tuple[str, int]:
    """A job bound to a made-up api_key_id. FK triggers are off for the insert only
    (superuser test DB): admission reads inference_jobs, never api_keys_new."""
    job_id = "0x" + secrets.token_hex(32)
    key_id = secrets.randbelow(2**40) + 2**41
    user_id = _insert_user(cur)
    cur.execute("SET LOCAL session_replication_role = replica")
    cur.execute(
        """
        INSERT INTO public.inference_jobs
            (job_id, user_id, api_key_id, spec_hash, cap_usd, spent_usd, deadline, status)
        VALUES (%s, %s, %s, %s, %s, %s, now() + interval '1 day', %s)
        """,
        (job_id, user_id, key_id, "0x" + "11" * 32, cap, spent, status),
    )
    cur.execute("SET LOCAL session_replication_role = origin")
    return job_id, key_id


def _admit(cur, key_id, max_inflight=4, ttl=900):
    cur.execute(
        "SELECT public.admit_job_request(p_api_key_id => %s, p_max_inflight => %s,"
        " p_ttl_seconds => %s)",
        (key_id, max_inflight, ttl),
    )
    return cur.fetchone()[0]


def _live(cur, job_id) -> int:
    cur.execute("SELECT count(*) FROM inference_job_leases WHERE job_id=%s", (job_id,))
    return cur.fetchone()[0]


def test_not_a_job_key_returns_null(db):
    with db.cursor() as cur:
        assert _admit(cur, 2**50 + secrets.randbelow(1000)) is None


def test_admits_up_to_max_inflight_then_busy(db):
    import psycopg

    with db.cursor() as cur:
        job, key = _job(cur)
        leases = [_admit(cur, key) for _ in range(4)]
        assert all(lease["job_id"] == job for lease in leases)
        assert len({lease["lease_id"] for lease in leases}) == 4
        cur.execute("SAVEPOINT s")
        with pytest.raises(psycopg.errors.RaiseException, match="job_busy"):
            _admit(cur, key)
        cur.execute("ROLLBACK TO SAVEPOINT s")
        cur.execute("SELECT public.release_job_request(%s, %s)", (job, leases[0]["lease_id"]))
        assert _live(cur, job) == 3
        assert _admit(cur, key)["job_id"] == job  # the freed slot is reusable


def test_expired_lease_is_swept(db):
    with db.cursor() as cur:
        job, key = _job(cur)
        _admit(cur, key, max_inflight=1)
        cur.execute(
            "UPDATE inference_job_leases SET expires_at = now() - interval '1 second'"
            " WHERE job_id=%s",
            (job,),
        )
        assert _admit(cur, key, max_inflight=1)["job_id"] == job
        assert _live(cur, job) == 1


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"cap": 1, "spent": 1}, "job_cap_reached"),
        ({"status": "closed"}, "job_not_running"),
    ],
)
def test_cap_and_status_are_rechecked_under_the_lock(db, kwargs, error):
    import psycopg

    with db.cursor() as cur:
        _, key = _job(cur, **kwargs)
        with pytest.raises(psycopg.errors.RaiseException, match=error):
            _admit(cur, key)


def test_backend_role_can_admit_but_not_touch_leases(db):
    import psycopg

    with db.cursor() as cur:
        job, key = _job(cur)
        cur.execute("SET LOCAL ROLE service_role")
        assert _admit(cur, key)["job_id"] == job
        cur.execute("RESET ROLE")
        for role in ("anon", "authenticated"):
            cur.execute("SAVEPOINT s")
            cur.execute(f"SET LOCAL ROLE {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _admit(cur, key)
            cur.execute("ROLLBACK TO SAVEPOINT s")


def test_concurrent_burst_gets_exactly_max_inflight(connect):
    setup = connect()
    with setup.cursor() as cur:
        with setup.transaction():
            job, key = _job(cur)
    admitted, busy, other = [], [], []

    def worker():
        try:
            with connect().cursor() as cur:
                admitted.append(_admit(cur, key))
        except Exception as e:  # noqa: BLE001 - sorted and asserted below
            (busy if "job_busy" in str(e) else other).append(e)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert other == []
        assert len(admitted) == 4 and len(busy) == 16
        with setup.cursor() as cur:
            assert _live(cur, job) == 4
    finally:
        with setup.cursor() as cur:
            cur.execute("SELECT user_id FROM inference_jobs WHERE job_id=%s", (job,))
            user_id = cur.fetchone()[0]
            cur.execute("DELETE FROM public.users WHERE id=%s", (user_id,))  # cascades
