"""Job cost disputes against the real migrated schema (CI-only).

Migration 20261127090000. Proves a line can be in only one live dispute, that a
released dispute frees its lines, the status and size constraints, cascades, and
that anon/authenticated cannot touch either table.
"""

from __future__ import annotations

import secrets

import psycopg
import pytest

from tests.sql.test_job_usage_v2_sql import _job

ROOT = "0x" + "ab" * 32


def _dispute(cur, job_id, lines=(0, 1), status="submitted"):
    dispute_id = "0x" + secrets.token_hex(32)
    cur.execute("SELECT user_id FROM inference_jobs WHERE job_id = %s", (job_id,))
    user_id = cur.fetchone()[0]
    cur.execute(
        """
        INSERT INTO public.job_disputes
            (dispute_id, job_id, user_id, usage_root, line_indexes, network,
             contract_address, policy_uri, status)
        VALUES (%s, %s, %s, %s, %s, 'studionet', '0xc', 'https://p', %s)
        """,
        (dispute_id, job_id, user_id, ROOT, list(lines), status),
    )
    for i in lines:
        cur.execute(
            "INSERT INTO public.job_dispute_lines (job_id, line_index, dispute_id)"
            " VALUES (%s, %s, %s)",
            (job_id, i, dispute_id),
        )
    return dispute_id


def test_a_line_is_in_one_live_dispute(db):
    with db.cursor() as cur:
        job = _job(cur)
        _dispute(cur, job, lines=(0, 1))
        cur.execute("SAVEPOINT s")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _dispute(cur, job, lines=(1, 2))
        cur.execute("ROLLBACK TO SAVEPOINT s")
        _dispute(cur, job, lines=(2,))  # other lines are free


def test_released_lines_can_be_disputed_again(db):
    with db.cursor() as cur:
        job = _job(cur)
        first = _dispute(cur, job, lines=(0,))
        cur.execute("DELETE FROM job_dispute_lines WHERE dispute_id = %s", (first,))
        cur.execute("UPDATE job_disputes SET status = 'error' WHERE dispute_id = %s", (first,))
        _dispute(cur, job, lines=(0,))


@pytest.mark.parametrize(
    "column,value",
    [("status", "refunded"), ("line_indexes", []), ("line_indexes", list(range(11)))],
)
def test_constraints(db, column, value):
    with db.cursor() as cur:
        job = _job(cur)
        d = _dispute(cur, job)
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(f"UPDATE job_disputes SET {column} = %s WHERE dispute_id = %s", (value, d))


def test_deleting_the_job_removes_its_disputes(db):
    with db.cursor() as cur:
        job = _job(cur)
        d = _dispute(cur, job)
        cur.execute("DELETE FROM inference_jobs WHERE job_id = %s", (job,))
        cur.execute("SELECT count(*) FROM job_disputes WHERE dispute_id = %s", (d,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM job_dispute_lines WHERE dispute_id = %s", (d,))
        assert cur.fetchone()[0] == 0


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_public_roles_cannot_read_or_write(db, role):
    with db.cursor() as cur:
        job = _job(cur)
        _dispute(cur, job)
        for sql in ("SELECT * FROM job_disputes", "SELECT * FROM job_dispute_lines"):
            cur.execute("SAVEPOINT s")
            cur.execute(f"SET LOCAL ROLE {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(sql)
            cur.execute("ROLLBACK TO SAVEPOINT s")
