"""append_job_usage + price_sheets against the real migrated schema (CI-only).

Migration 20261023000000. Proves what unit tests with a mocked RPC cannot:
the function accepts the backend's calls (old 3-arg and new v2 shapes), keeps
seq gapless under concurrency, refuses a price sheet that does not hash to its
key, and the grants leave published sheets immutable.
"""

from __future__ import annotations

import json
import secrets
import threading

import pytest

from src.services import job_usage_v2 as v2

SHEET_HASH, SHEET_BODY = v2.price_sheet("openai/gpt-5-mini", 0.25e-6, 2e-6, 1.0)
SHEET_BODY_S = SHEET_BODY.decode()

V1 = {
    "ts": "2026-10-27T00:00:00Z",
    "model": "openai/gpt-5-mini",
    "provider": "openai",
    "tokens_in": 1,
    "tokens_out": 1,
    "cost_usd": "0.01",
    "commit": "c",
}


def _v2_entry(request_id="req_1", sheet=SHEET_HASH, content="0x" + "aa" * 32):
    return {
        "request_id": request_id,
        "ts": V1["ts"],
        "model": V1["model"],
        "provider": V1["provider"],
        "tokens_in": 1,
        "tokens_out": 1,
        "tokens_reasoning": 0,
        "cost_usd": "0.01",
        "status": 200,
        "finish_reason": "stop",
        "price_sheet": sheet,
        "content_hash": content,
        "commit": "c",
    }


def _insert_user(cur) -> int:
    """A users row with every NOT NULL, no-default column filled generically."""
    cur.execute(
        """
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'users'
          AND is_nullable = 'NO' AND column_default IS NULL
        """
    )
    cols, vals = [], []
    for name, dtype in cur.fetchall():
        cols.append(f'"{name}"')
        if "int" in dtype or dtype in ("numeric", "double precision", "real"):
            vals.append(0)
        elif dtype == "boolean":
            vals.append(False)
        elif "timestamp" in dtype or dtype == "date":
            vals.append("2026-01-01")
        elif dtype == "jsonb" or dtype == "json":
            vals.append("{}")
        elif dtype == "uuid":
            vals.append("00000000-0000-0000-0000-000000000001")
        else:
            vals.append(f"sqltest-{secrets.token_hex(4)}")
    if cols:
        cur.execute(
            f"INSERT INTO public.users ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(vals))})"
            " RETURNING id",
            vals,
        )
    else:
        cur.execute("INSERT INTO public.users DEFAULT VALUES RETURNING id")
    return cur.fetchone()[0]


def _job(cur, status="running") -> str:
    job_id = "0x" + secrets.token_hex(32)
    user_id = _insert_user(cur)
    cur.execute(
        """
        INSERT INTO public.inference_jobs (job_id, user_id, spec_hash, cap_usd, deadline, status)
        VALUES (%s, %s, %s, 10, now() + interval '1 day', %s)
        """,
        (job_id, user_id, "0x" + "11" * 32, status),
    )
    return job_id


def _append(cur, job_id, entry_v2=None, body=None, estimated=False):
    if entry_v2 is None:
        cur.execute(
            "SELECT public.append_job_usage(p_job_id => %s, p_entry => %s, p_cost => %s)",
            (job_id, json.dumps(V1), V1["cost_usd"]),
        )
    else:
        cur.execute(
            """
            SELECT public.append_job_usage(
                p_job_id => %s, p_entry => %s, p_cost => %s,
                p_entry_v2 => %s, p_usage_estimated => %s, p_price_sheet_body => %s)
            """,
            (job_id, json.dumps(V1), V1["cost_usd"], json.dumps(entry_v2), estimated, body),
        )
    return cur.fetchone()[0]


def test_old_three_argument_call_still_works(db):
    with db.cursor() as cur:
        job = _job(cur)
        assert [_append(cur, job) for _ in range(3)] == [0, 1, 2]
        cur.execute(
            "SELECT entry_v2, usage_estimated FROM inference_job_usage WHERE job_id=%s", (job,)
        )
        assert cur.fetchall() == [(None, False)] * 3


def test_v2_line_stores_entry_sheet_and_request_id(db):
    with db.cursor() as cur:
        job = _job(cur)
        assert _append(cur, job, _v2_entry(), SHEET_BODY_S, estimated=True) == 0
        cur.execute(
            "SELECT entry_v2, usage_estimated, request_id FROM inference_job_usage WHERE job_id=%s",
            (job,),
        )
        entry_v2, estimated, request_id = cur.fetchone()
        assert entry_v2 == _v2_entry() and estimated is True and request_id == "req_1"
        cur.execute("SELECT body FROM price_sheets WHERE hash=%s", (SHEET_HASH,))
        # Byte-identical to what Python hashed: Postgres sha256 agrees.
        assert cur.fetchone()[0] == SHEET_BODY_S
        cur.execute("SELECT spent_usd FROM inference_jobs WHERE job_id=%s", (job,))
        assert float(cur.fetchone()[0]) == pytest.approx(0.01)


def test_same_sheet_twice_is_stored_once(db):
    with db.cursor() as cur:
        job = _job(cur)
        _append(cur, job, _v2_entry("a"), SHEET_BODY_S)
        _append(cur, job, _v2_entry("b"), SHEET_BODY_S)
        cur.execute("SELECT count(*) FROM price_sheets WHERE hash=%s", (SHEET_HASH,))
        assert cur.fetchone()[0] == 1


def test_sheet_that_does_not_hash_to_its_key_is_refused(db):
    import psycopg

    with db.cursor() as cur:
        job = _job(cur)
        forged = SHEET_BODY_S.replace("0.25", "0.01")
        with pytest.raises(psycopg.errors.CheckViolation):
            _append(cur, job, _v2_entry(), forged)


def test_unknown_sheet_without_body_is_refused(db):
    import psycopg

    with db.cursor() as cur:
        job = _job(cur)
        with pytest.raises(psycopg.errors.RaiseException, match="price_sheet_unknown"):
            _append(cur, job, _v2_entry(sheet="0x" + "77" * 32), None)


def test_zero_sheet_means_unlisted_and_is_accepted(db):
    with db.cursor() as cur:
        job = _job(cur)
        assert _append(cur, job, _v2_entry(sheet=v2.ZERO_HASH), None) == 0


@pytest.mark.parametrize(
    "bad",
    [{"price_sheet": "nope"}, {"content_hash": "0xZZ"}, {"content_hash": None}],
)
def test_malformed_v2_entry_is_refused(db, bad):
    import psycopg

    with db.cursor() as cur:
        job = _job(cur)
        with pytest.raises(psycopg.errors.RaiseException, match="entry_v2_invalid"):
            _append(cur, job, {**_v2_entry(), **bad}, SHEET_BODY_S)


def test_closed_job_takes_no_lines(db):
    import psycopg

    with db.cursor() as cur:
        job = _job(cur, status="closed")
        with pytest.raises(psycopg.errors.RaiseException, match="job_not_running"):
            _append(cur, job, _v2_entry(), SHEET_BODY_S)


def test_concurrent_appends_are_gapless(connect):
    setup = connect()
    with setup.cursor() as cur:
        job = _job(cur)
    errors: list[Exception] = []

    def worker(i):
        try:
            with connect().cursor() as cur:
                _append(cur, job, _v2_entry(f"req_{i}"), SHEET_BODY_S)
        except Exception as e:  # noqa: BLE001 - collected and asserted below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert errors == []
        with setup.cursor() as cur:
            cur.execute("SELECT seq FROM inference_job_usage WHERE job_id=%s ORDER BY seq", (job,))
            assert [r[0] for r in cur.fetchall()] == list(range(20))
            cur.execute("SELECT spent_usd FROM inference_jobs WHERE job_id=%s", (job,))
            assert float(cur.fetchone()[0]) == pytest.approx(0.20)
    finally:
        with setup.cursor() as cur:
            cur.execute("SELECT user_id FROM inference_jobs WHERE job_id=%s", (job,))
            user_id = cur.fetchone()[0]
            cur.execute("DELETE FROM public.users WHERE id=%s", (user_id,))  # cascades


def test_published_sheets_are_immutable_for_the_backend(db):
    import psycopg

    with db.cursor() as cur:
        # Other tests may already have committed this sheet; sheets are shared.
        cur.execute(
            "INSERT INTO price_sheets (hash, body) VALUES (%s, %s) ON CONFLICT (hash) DO NOTHING",
            (SHEET_HASH, SHEET_BODY_S),
        )
        cur.execute("SAVEPOINT s")
        cur.execute("SET LOCAL ROLE service_role")
        cur.execute("SELECT body FROM price_sheets WHERE hash=%s", (SHEET_HASH,))
        assert cur.fetchone()[0] == SHEET_BODY_S
        for stmt in (
            "UPDATE price_sheets SET created_at = now()",
            "DELETE FROM price_sheets",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(stmt)
            cur.execute("ROLLBACK TO SAVEPOINT s")
            cur.execute("SET LOCAL ROLE service_role")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_public_roles_cannot_read_sheets_or_append(db, role):
    import psycopg

    with db.cursor() as cur:
        job = _job(cur)
        cur.execute("SAVEPOINT s")
        cur.execute(f"SET LOCAL ROLE {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("SELECT * FROM price_sheets")
        cur.execute("ROLLBACK TO SAVEPOINT s")
        cur.execute(f"SET LOCAL ROLE {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _append(cur, job)
