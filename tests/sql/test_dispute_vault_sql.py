"""Dispute content vault against the real migrated schema (CI-only).

Migration 20261027000000. Proves the window rule (72 h after close, or after the
deadline), the hourly purge, cascades, and that stored ciphertext can be deleted
but never rewritten.
"""

from __future__ import annotations

import secrets

import pytest

from tests.sql.test_job_usage_v2_sql import _job

HASH = "0x" + "ab" * 32


def _content(cur, job_id, request_id=None):
    request_id = request_id or f"req-{secrets.token_hex(4)}"
    cur.execute(
        """
        INSERT INTO public.dispute_content (request_id, job_id, content_hash, key_id, nonce, ciphertext)
        VALUES (%s, %s, %s, 'k1', %s, %s)
        """,
        (request_id, job_id, HASH, b"\x00" * 12, b"\x01" * 64),
    )
    return request_id


def test_window_ends_72h_after_close_or_deadline(db):
    with db.cursor() as cur:
        job = _job(cur)
        cur.execute(
            "SELECT dispute_content_expiry(%s) - deadline FROM inference_jobs WHERE job_id=%s",
            (job, job),
        )
        assert str(cur.fetchone()[0]) == "3 days, 0:00:00"
        cur.execute(
            "UPDATE inference_jobs SET closed_at = now() - interval '1 hour' WHERE job_id=%s",
            (job,),
        )
        cur.execute("SELECT dispute_content_expiry(%s) > now() + interval '70 hours'", (job,))
        assert cur.fetchone()[0] is True


def test_purge_removes_only_content_past_its_window(db):
    with db.cursor() as cur:
        old, live = _job(cur), _job(cur)
        old_req, live_req = _content(cur, old), _content(cur, live)
        cur.execute(
            "UPDATE inference_jobs SET status='closed', closed_at = now() - interval '73 hours' WHERE job_id=%s",
            (old,),
        )
        cur.execute(
            "INSERT INTO dispute_reveals (token_hash, request_id, expires_at) VALUES (%s, %s, now() - interval '1 minute')",
            (secrets.token_hex(32), live_req),
        )
        cur.execute("SELECT purge_dispute_content()")
        out = cur.fetchone()[0]
        assert out["content"] == 1 and out["reveals"] == 1
        cur.execute(
            "SELECT request_id FROM dispute_content WHERE request_id IN (%s, %s)",
            (old_req, live_req),
        )
        assert [r[0] for r in cur.fetchall()] == [live_req]


def test_reveals_go_with_their_content(db):
    with db.cursor() as cur:
        job = _job(cur)
        req = _content(cur, job)
        cur.execute(
            "INSERT INTO dispute_reveals (token_hash, request_id, expires_at) VALUES (%s, %s, now() + interval '1 day')",
            (secrets.token_hex(32), req),
        )
        cur.execute("DELETE FROM dispute_content WHERE request_id=%s", (req,))
        cur.execute("SELECT count(*) FROM dispute_reveals WHERE request_id=%s", (req,))
        assert cur.fetchone()[0] == 0


def test_oversized_ciphertext_is_refused(db):
    psycopg = pytest.importorskip("psycopg")
    with db.cursor() as cur:
        job = _job(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO dispute_content (request_id, job_id, content_hash, key_id, nonce, ciphertext)
                VALUES ('big', %s, %s, 'k1', %s, %s)
                """,
                (job, HASH, b"\x00" * 12, b"\x01" * 256_017),
            )


def test_service_role_can_delete_but_never_rewrite(db):
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT privilege_type FROM information_schema.role_table_grants
            WHERE grantee = 'service_role' AND table_name = 'dispute_content'
            ORDER BY 1
            """
        )
        assert [r[0] for r in cur.fetchall()] == ["DELETE", "INSERT", "SELECT"]
        cur.execute(
            """
            SELECT count(*) FROM information_schema.role_table_grants
            WHERE grantee IN ('anon', 'authenticated') AND table_name IN ('dispute_content', 'dispute_reveals')
            """
        )
        assert cur.fetchone()[0] == 0
