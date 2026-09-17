"""Tests for src/routes/admin_coupons.py (admin coupons API).

Two things these tests care about more than coverage:

1. A broken query must never render as an empty list or a zero. Every read
   has a "db raises -> 503, not []" case, per
   "Gatewayz - Phantom Column Failures, Admin Dashboard Batch 1 (Sep 15, 2026)".
2. Every redemption invariant has a test that would catch its removal. A
   coupon that can be redeemed more times than intended is money.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.db.coupons import RedemptionScanTooLarge
from src.main import app
from src.security.deps import require_admin

client = TestClient(app)

SUPERADMIN = {"id": 1, "email": "root@example.com", "role": "superadmin"}
ADMIN = {"id": 2, "email": "admin@example.com", "role": "admin"}

NOW = datetime(2026, 9, 16, tzinfo=UTC)
FUTURE = NOW + timedelta(days=30)
PAST = NOW - timedelta(days=30)


def make_coupon(**overrides):
    """A stored `coupons` row. Only columns verified to exist in prod."""
    row = {
        "id": 19,
        "code": "GATEWAYZ",
        "description": "Gatewayz Coupon",
        "coupon_type": "referral",
        "coupon_scope": "global",
        "value_usd": 20.00,
        "assigned_to_user_id": None,
        "max_uses": 2,
        "times_used": 0,
        "valid_from": PAST.isoformat(),
        "valid_until": FUTURE.isoformat(),
        "is_active": True,
        "created_by": 34,
        "created_by_type": "admin",
        "created_at": PAST.isoformat(),
        "updated_at": PAST.isoformat(),
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _isolate_dependency_overrides():
    """Snapshot and restore the FULL app.dependency_overrides dict around
    every test -- same reason as tests/routes/test_admin_staff.py: another
    module can leave an override set, and only restoring the exact prior dict
    makes this file independent of run order."""
    saved = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture
def admin_override():
    def _set(user=None):
        app.dependency_overrides[require_admin] = lambda: user or ADMIN

    _set()
    return _set


@pytest.fixture(autouse=True)
def _mute_audit():
    """record_audit writes to Supabase; it is asserted on explicitly where it
    matters and must not attempt a network call anywhere else."""
    with patch("src.routes.admin_coupons.record_audit") as mock:
        yield mock


class TestAuth:
    def test_list_requires_admin(self):
        assert client.get("/admin/coupons").status_code in (401, 403)

    def test_detail_requires_admin(self):
        assert client.get("/admin/coupons/19").status_code in (401, 403)

    def test_analytics_requires_admin(self):
        assert client.get("/admin/coupons/19/analytics").status_code in (401, 403)

    def test_stats_requires_admin(self):
        assert client.get("/admin/coupons/stats/overview").status_code in (401, 403)

    def test_create_requires_admin(self):
        response = client.post("/admin/coupons", json={"code": "X", "value_usd": 1, "max_uses": 1})
        assert response.status_code in (401, 403)

    def test_update_requires_admin(self):
        assert client.patch("/admin/coupons/19", json={"is_active": True}).status_code in (401, 403)

    def test_delete_requires_admin(self):
        assert client.delete("/admin/coupons/19").status_code in (401, 403)


class TestListCoupons:
    def test_returns_panel_list_shape(self, admin_override):
        with patch(
            "src.routes.admin_coupons.list_coupons", return_value=([make_coupon()], 7)
        ) as mock:
            response = client.get("/admin/coupons?limit=10&offset=0")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 7
        assert body["limit"] == 10
        assert body["offset"] == 0
        assert len(body["coupons"]) == 1
        coupon = body["coupons"][0]
        assert coupon["code"] == "GATEWAYZ"
        assert coupon["value_usd"] == 20.0
        assert coupon["coupon_scope"] == "global"
        assert coupon["times_used"] == 0
        mock.assert_called_once()

    def test_total_is_the_table_count_not_the_page_length(self, admin_override):
        """A page of 1 out of 7 must report 7 -- len(rows) would under-report
        every filtered view the panel draws its summary from."""
        with patch("src.routes.admin_coupons.list_coupons", return_value=([make_coupon()], 7)):
            body = client.get("/admin/coupons?limit=1").json()
        assert body["total"] == 7 and len(body["coupons"]) == 1

    def test_filters_are_forwarded(self, admin_override):
        with patch("src.routes.admin_coupons.list_coupons", return_value=([], 0)) as mock:
            client.get(
                "/admin/coupons?scope=global&coupon_type=referral&is_active=true&search=gate"
            )
        kwargs = mock.call_args.kwargs
        assert kwargs["scope"] == "global"
        assert kwargs["coupon_type"] == "referral"
        assert kwargs["is_active"] is True
        assert kwargs["search"] == "gate"

    def test_invalid_scope_rejected(self, admin_override):
        response = client.get("/admin/coupons?scope=everyone")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_coupon_scope"

    def test_invalid_type_rejected(self, admin_override):
        response = client.get("/admin/coupons?coupon_type=freebie")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_coupon_type"

    def test_db_failure_returns_503_not_an_empty_list(self, admin_override):
        with patch("src.routes.admin_coupons.list_coupons", side_effect=Exception("42703")):
            response = client.get("/admin/coupons")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "coupons_unavailable"


class TestGetCoupon:
    def test_returns_coupon(self, admin_override):
        with patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon()):
            response = client.get("/admin/coupons/19")
        assert response.status_code == 200
        assert response.json()["id"] == 19

    def test_missing_coupon_is_404(self, admin_override):
        with patch("src.routes.admin_coupons.get_coupon", return_value=None):
            response = client.get("/admin/coupons/999")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "coupon_not_found"

    def test_db_failure_is_503_not_404(self, admin_override):
        """A broken lookup must not masquerade as 'no such coupon'."""
        with patch("src.routes.admin_coupons.get_coupon", side_effect=Exception("boom")):
            response = client.get("/admin/coupons/19")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "coupon_lookup_unavailable"


class TestAnalytics:
    def test_returns_panel_analytics_shape(self, admin_override):
        stats = {"total_redemptions": 3, "unique_users": 2, "total_value_distributed": 60.0}
        with (
            patch(
                "src.routes.admin_coupons.get_coupon",
                return_value=make_coupon(max_uses=4, times_used=1),
            ),
            patch("src.routes.admin_coupons.get_redemption_stats", return_value=stats),
        ):
            response = client.get("/admin/coupons/19/analytics")

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "coupon",
            "total_redemptions",
            "unique_users",
            "total_value_distributed",
            "redemption_rate",
            "remaining_uses",
            "is_expired",
        }
        assert body["total_redemptions"] == 3
        assert body["unique_users"] == 2
        assert body["total_value_distributed"] == 60.0
        assert body["remaining_uses"] == 3
        assert body["redemption_rate"] == 25.0
        assert body["is_expired"] is False

    def test_expired_coupon_flagged(self, admin_override):
        stats = {"total_redemptions": 0, "unique_users": 0, "total_value_distributed": 0.0}
        with (
            patch(
                "src.routes.admin_coupons.get_coupon",
                return_value=make_coupon(valid_until=PAST.isoformat()),
            ),
            patch("src.routes.admin_coupons.get_redemption_stats", return_value=stats),
        ):
            body = client.get("/admin/coupons/19/analytics").json()
        assert body["is_expired"] is True

    def test_fully_redeemed_coupon_reports_zero_remaining(self, admin_override):
        stats = {"total_redemptions": 2, "unique_users": 2, "total_value_distributed": 40.0}
        with (
            patch(
                "src.routes.admin_coupons.get_coupon",
                return_value=make_coupon(max_uses=2, times_used=2),
            ),
            patch("src.routes.admin_coupons.get_redemption_stats", return_value=stats),
        ):
            body = client.get("/admin/coupons/19/analytics").json()
        assert body["remaining_uses"] == 0
        assert body["redemption_rate"] == 100.0

    def test_truncated_scan_is_503_not_a_short_total(self, admin_override):
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon()),
            patch(
                "src.routes.admin_coupons.get_redemption_stats",
                side_effect=RedemptionScanTooLarge("too many"),
            ),
        ):
            response = client.get("/admin/coupons/19/analytics")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "coupon_analytics_unavailable"


class TestStatsOverview:
    def test_stats_path_is_not_parsed_as_a_coupon_id(self, admin_override):
        """/admin/coupons/stats/overview must reach the stats handler, not
        422 on int('stats') against /admin/coupons/{coupon_id}."""
        counts = {
            "total_coupons": 3,
            "active_coupons": 2,
            "global_coupons": 2,
            "user_specific_coupons": 1,
        }
        stats = {"total_redemptions": 4, "unique_users": 3, "total_value_distributed": 80.0}
        with (
            patch("src.routes.admin_coupons.get_coupon_counts", return_value=counts),
            patch("src.routes.admin_coupons.get_redemption_stats", return_value=stats),
        ):
            response = client.get("/admin/coupons/stats/overview")

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "total_coupons": 3,
            "active_coupons": 2,
            "user_specific_coupons": 1,
            "global_coupons": 2,
            "total_redemptions": 4,
            "unique_redeemers": 3,
            "total_value_distributed": 80.0,
            "average_redemption_value": 20.0,
        }

    def test_zero_redemptions_does_not_divide_by_zero(self, admin_override):
        counts = {
            "total_coupons": 1,
            "active_coupons": 1,
            "global_coupons": 1,
            "user_specific_coupons": 0,
        }
        stats = {"total_redemptions": 0, "unique_users": 0, "total_value_distributed": 0.0}
        with (
            patch("src.routes.admin_coupons.get_coupon_counts", return_value=counts),
            patch("src.routes.admin_coupons.get_redemption_stats", return_value=stats),
        ):
            body = client.get("/admin/coupons/stats/overview").json()
        assert body["average_redemption_value"] == 0.0

    def test_db_failure_returns_503_not_zeroes(self, admin_override):
        with patch("src.routes.admin_coupons.get_coupon_counts", side_effect=Exception("42703")):
            response = client.get("/admin/coupons/stats/overview")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "coupon_stats_unavailable"


def _create_body(**overrides):
    body = {
        "code": "welcome-50",
        "value_usd": 50,
        "max_uses": 10,
        "coupon_scope": "global",
        "coupon_type": "promotional",
        "valid_from": PAST.isoformat(),
        "valid_until": FUTURE.isoformat(),
    }
    body.update(overrides)
    return body


class TestCreateCoupon:
    def test_creates_and_uppercases_the_code(self, admin_override, _mute_audit):
        with (
            patch("src.routes.admin_coupons.get_coupon_by_code", return_value=None),
            patch(
                "src.routes.admin_coupons.create_coupon",
                return_value=make_coupon(id=42, code="WELCOME-50"),
            ) as mock_create,
        ):
            response = client.post("/admin/coupons", json=_create_body())

        assert response.status_code == 201
        assert mock_create.call_args.args[0]["code"] == "WELCOME-50"
        assert response.json()["id"] == 42
        _mute_audit.assert_called_once()
        assert _mute_audit.call_args.kwargs["action"] == "coupon.created"

    def test_never_writes_times_used(self, admin_override):
        with (
            patch("src.routes.admin_coupons.get_coupon_by_code", return_value=None),
            patch("src.routes.admin_coupons.create_coupon", return_value=make_coupon()) as mock,
        ):
            client.post("/admin/coupons", json=_create_body())
        assert "times_used" not in mock.call_args.args[0]

    def test_caller_supplied_times_used_is_rejected(self, admin_override):
        """extra='forbid': a caller trying to seed the redemption counter gets
        a 422, not a silently dropped field."""
        response = client.post("/admin/coupons", json=_create_body(times_used=99))
        assert response.status_code == 422

    def test_duplicate_code_is_409(self, admin_override):
        with patch("src.routes.admin_coupons.get_coupon_by_code", return_value=make_coupon()):
            response = client.post("/admin/coupons", json=_create_body())
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "coupon_code_taken"

    def test_lost_uniqueness_race_is_409_not_503(self, admin_override):
        """_assert_code_available and the INSERT are two statements with no lock
        between them, so two concurrent creates of 'welcome' and 'WELCOME' both
        pass the pre-check. UNIQUE (UPPER(code)) stops the second, and it
        arrives here as a 23505. That is a conflict the caller can act on --
        reporting it as a 503 would send an admin chasing an outage that isn't
        one, and reporting it as 500 would be a regression in error quality."""
        error = Exception('duplicate key value violates unique constraint "uq_coupons_code_upper"')
        with (
            patch("src.routes.admin_coupons.get_coupon_by_code", return_value=None),
            patch("src.routes.admin_coupons.create_coupon", side_effect=error),
        ):
            response = client.post("/admin/coupons", json=_create_body())

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "coupon_code_taken"

    def test_case_sensitive_unique_violation_is_also_409(self, admin_override):
        """The pre-existing inline UNIQUE(code) is named coupons_code_key; a
        violation of it means the same thing to the caller."""
        error = Exception('duplicate key value violates unique constraint "coupons_code_key"')
        with (
            patch("src.routes.admin_coupons.get_coupon_by_code", return_value=None),
            patch("src.routes.admin_coupons.create_coupon", side_effect=error),
        ):
            response = client.post("/admin/coupons", json=_create_body())
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "coupon_code_taken"

    def test_an_unrelated_unique_violation_is_not_reported_as_a_taken_code(self, admin_override):
        """The narrowing that matters. A unique violation on some OTHER index is
        not "your code is taken" -- saying so would send an admin hunting for a
        duplicate that does not exist, which is the confident-wrong-reason
        failure this module exists to avoid."""
        error = Exception('duplicate key value violates unique constraint "some_other_idx"')
        with (
            patch("src.routes.admin_coupons.get_coupon_by_code", return_value=None),
            patch("src.routes.admin_coupons.create_coupon", side_effect=error),
        ):
            response = client.post("/admin/coupons", json=_create_body())
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "coupon_create_failed"

    def test_a_plain_outage_is_still_503(self, admin_override):
        with (
            patch("src.routes.admin_coupons.get_coupon_by_code", return_value=None),
            patch(
                "src.routes.admin_coupons.create_coupon",
                side_effect=RuntimeError("connection reset"),
            ),
        ):
            response = client.post("/admin/coupons", json=_create_body())
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "coupon_create_failed"

    def test_duplicate_check_is_case_insensitive_on_the_normalized_code(self, admin_override):
        with patch("src.routes.admin_coupons.get_coupon_by_code", return_value=None) as mock_lookup:
            with patch("src.routes.admin_coupons.create_coupon", return_value=make_coupon()):
                client.post("/admin/coupons", json=_create_body(code="GaTeWaYz"))
        assert mock_lookup.call_args.args[0] == "GATEWAYZ"

    @pytest.mark.parametrize(
        "overrides,expected_code",
        [
            ({"value_usd": 0}, None),  # pydantic gt=0
            ({"value_usd": 1001}, None),  # pydantic le=1000
            ({"max_uses": 0}, None),  # pydantic gt=0
            ({"coupon_scope": "user_specific", "max_uses": 1}, "assigned_user_required"),
            (
                {"coupon_scope": "user_specific", "assigned_to_user_id": 7, "max_uses": 2},
                "user_specific_max_uses",
            ),
            ({"assigned_to_user_id": 7}, "assigned_user_not_allowed"),
            ({"valid_until": None}, "valid_until_required"),
            (
                {"valid_from": FUTURE.isoformat(), "valid_until": PAST.isoformat()},
                "invalid_validity_window",
            ),
            (
                {"valid_from": FUTURE.isoformat(), "valid_until": FUTURE.isoformat()},
                "invalid_validity_window",
            ),
            ({"code": "  "}, "code_required"),
            ({"code": "SPACE CODE"}, "code_invalid_characters"),
            ({"code": "A" * 51}, "code_too_long"),
        ],
    )
    def test_invariants_rejected(self, admin_override, overrides, expected_code):
        with (
            patch("src.routes.admin_coupons.get_coupon_by_code", return_value=None),
            patch("src.routes.admin_coupons.create_coupon") as mock_create,
        ):
            response = client.post("/admin/coupons", json=_create_body(**overrides))

        assert response.status_code == 422
        mock_create.assert_not_called()
        if expected_code:
            assert response.json()["error"]["code"] == expected_code

    def test_user_specific_coupon_is_accepted(self, admin_override):
        with (
            patch("src.routes.admin_coupons.get_coupon_by_code", return_value=None),
            patch(
                "src.routes.admin_coupons.create_coupon",
                return_value=make_coupon(
                    coupon_scope="user_specific", assigned_to_user_id=7, max_uses=1
                ),
            ),
        ):
            response = client.post(
                "/admin/coupons",
                json=_create_body(coupon_scope="user_specific", assigned_to_user_id=7, max_uses=1),
            )
        assert response.status_code == 201

    def test_insert_failure_is_503(self, admin_override):
        with (
            patch("src.routes.admin_coupons.get_coupon_by_code", return_value=None),
            patch("src.routes.admin_coupons.create_coupon", side_effect=Exception("23514")),
        ):
            response = client.post("/admin/coupons", json=_create_body())
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "coupon_create_failed"


class TestUpdateCoupon:
    def test_partial_patch_reactivates(self, admin_override, _mute_audit):
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon(is_active=False)),
            patch(
                "src.routes.admin_coupons.update_coupon", return_value=make_coupon(is_active=True)
            ) as mock,
        ):
            response = client.patch("/admin/coupons/19", json={"is_active": True})

        assert response.status_code == 200
        assert mock.call_args.args[1] == {"is_active": True}
        assert response.json()["is_active"] is True
        assert _mute_audit.call_args.kwargs["action"] == "coupon.updated"

    def test_rename_into_an_existing_code_is_409_not_503(self, admin_override):
        """Renaming a coupon into a code another row holds collides the same way
        a create does, and for the same reason: the pre-check is not atomic with
        the write."""
        error = Exception('duplicate key value violates unique constraint "uq_coupons_code_upper"')
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon()),
            patch("src.routes.admin_coupons.get_coupon_by_code", return_value=None),
            patch("src.routes.admin_coupons.update_coupon", side_effect=error),
        ):
            response = client.patch("/admin/coupons/19", json={"code": "WELCOME"})

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "coupon_code_taken"

    def test_update_outage_is_still_503(self, admin_override):
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon()),
            patch(
                "src.routes.admin_coupons.update_coupon",
                side_effect=RuntimeError("connection reset"),
            ),
        ):
            response = client.patch("/admin/coupons/19", json={"is_active": True})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "coupon_update_failed"

    def test_put_is_accepted_as_well_as_patch(self, admin_override):
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon()),
            patch("src.routes.admin_coupons.update_coupon", return_value=make_coupon()),
        ):
            response = client.put("/admin/coupons/19", json={"is_active": True})
        assert response.status_code == 200

    def test_cannot_lower_max_uses_below_times_used(self, admin_override):
        """The money invariant: 5 redemptions already granted, ceiling cannot
        be moved to 3."""
        with (
            patch(
                "src.routes.admin_coupons.get_coupon",
                return_value=make_coupon(max_uses=10, times_used=5),
            ),
            patch("src.routes.admin_coupons.update_coupon") as mock,
        ):
            response = client.patch("/admin/coupons/19", json={"max_uses": 3})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "max_uses_below_times_used"
        mock.assert_not_called()

    def test_can_raise_max_uses(self, admin_override):
        with (
            patch(
                "src.routes.admin_coupons.get_coupon",
                return_value=make_coupon(max_uses=2, times_used=2),
            ),
            patch("src.routes.admin_coupons.update_coupon", return_value=make_coupon(max_uses=5)),
        ):
            response = client.patch("/admin/coupons/19", json={"max_uses": 5})
        assert response.status_code == 200

    def test_times_used_is_not_writable(self, admin_override):
        response = client.patch("/admin/coupons/19", json={"times_used": 0})
        assert response.status_code == 422

    def test_scope_switch_validated_against_merged_row(self, admin_override):
        """Switching a global coupon to user_specific without naming a user
        must fail even though the patch alone looks harmless."""
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon()),
            patch("src.routes.admin_coupons.update_coupon") as mock,
        ):
            response = client.patch("/admin/coupons/19", json={"coupon_scope": "user_specific"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "assigned_user_required"
        mock.assert_not_called()

    def test_window_validated_against_merged_row(self, admin_override):
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon()),
            patch("src.routes.admin_coupons.update_coupon") as mock,
        ):
            response = client.patch(
                "/admin/coupons/19", json={"valid_until": (PAST - timedelta(days=1)).isoformat()}
            )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_validity_window"
        mock.assert_not_called()

    def test_renaming_onto_an_existing_code_is_409(self, admin_override):
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon()),
            patch(
                "src.routes.admin_coupons.get_coupon_by_code",
                return_value=make_coupon(id=77, code="TAKEN"),
            ),
            patch("src.routes.admin_coupons.update_coupon") as mock,
        ):
            response = client.patch("/admin/coupons/19", json={"code": "taken"})
        assert response.status_code == 409
        mock.assert_not_called()

    def test_same_code_recased_is_not_a_conflict(self, admin_override):
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon()),
            patch("src.routes.admin_coupons.get_coupon_by_code") as lookup,
            patch("src.routes.admin_coupons.update_coupon", return_value=make_coupon()),
        ):
            response = client.patch("/admin/coupons/19", json={"code": "gatewayz"})
        assert response.status_code == 200
        lookup.assert_not_called()

    def test_missing_coupon_is_404(self, admin_override):
        with patch("src.routes.admin_coupons.get_coupon", return_value=None):
            response = client.patch("/admin/coupons/999", json={"is_active": True})
        assert response.status_code == 404


class TestDeleteCoupon:
    def test_default_delete_deactivates_and_keeps_the_row(self, admin_override, _mute_audit):
        """The panel's Deactivate button calls DELETE and then offers
        Reactivate, so the row has to survive."""
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon()),
            patch(
                "src.routes.admin_coupons.update_coupon", return_value=make_coupon(is_active=False)
            ) as mock_update,
            patch("src.routes.admin_coupons.delete_coupon") as mock_delete,
        ):
            response = client.delete("/admin/coupons/19")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "deactivated" in body["message"]
        assert body["coupon"]["is_active"] is False
        assert mock_update.call_args.args[1] == {"is_active": False}
        mock_delete.assert_not_called()
        assert _mute_audit.call_args.kwargs["action"] == "coupon.deactivated"

    def test_hard_delete_requires_superadmin(self, admin_override):
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon()),
            patch("src.routes.admin_coupons.delete_coupon") as mock_delete,
        ):
            response = client.delete("/admin/coupons/19?hard=true")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "superadmin_required"
        mock_delete.assert_not_called()

    def test_superadmin_hard_delete_of_an_unredeemed_coupon(self, admin_override, _mute_audit):
        admin_override(SUPERADMIN)
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon(times_used=0)),
            patch("src.routes.admin_coupons.count_redemptions", return_value=0),
            patch("src.routes.admin_coupons.delete_coupon", return_value=True) as mock_delete,
        ):
            response = client.delete("/admin/coupons/19?hard=true")

        assert response.status_code == 200
        assert "permanently deleted" in response.json()["message"]
        mock_delete.assert_called_once_with(19)
        assert _mute_audit.call_args.kwargs["action"] == "coupon.deleted"

    def test_hard_delete_refused_once_redeemed(self, admin_override):
        """coupon_redemptions.coupon_id is ON DELETE CASCADE -- deleting a
        redeemed coupon would take the record of the money with it."""
        admin_override(SUPERADMIN)
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon(times_used=1)),
            patch("src.routes.admin_coupons.count_redemptions", return_value=1),
            patch("src.routes.admin_coupons.delete_coupon") as mock_delete,
        ):
            response = client.delete("/admin/coupons/19?hard=true")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "coupon_has_redemptions"
        mock_delete.assert_not_called()

    def test_hard_delete_refused_when_ledger_disagrees_with_counter(self, admin_override):
        """times_used says 0 but the ledger has rows: still refuse."""
        admin_override(SUPERADMIN)
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon(times_used=0)),
            patch("src.routes.admin_coupons.count_redemptions", return_value=3),
            patch("src.routes.admin_coupons.delete_coupon") as mock_delete,
        ):
            response = client.delete("/admin/coupons/19?hard=true")
        assert response.status_code == 409
        mock_delete.assert_not_called()

    def test_unknown_redemption_count_fails_closed(self, admin_override):
        admin_override(SUPERADMIN)
        with (
            patch("src.routes.admin_coupons.get_coupon", return_value=make_coupon()),
            patch("src.routes.admin_coupons.count_redemptions", side_effect=Exception("boom")),
            patch("src.routes.admin_coupons.delete_coupon") as mock_delete,
        ):
            response = client.delete("/admin/coupons/19?hard=true")
        assert response.status_code == 503
        mock_delete.assert_not_called()

    def test_missing_coupon_is_404(self, admin_override):
        with patch("src.routes.admin_coupons.get_coupon", return_value=None):
            response = client.delete("/admin/coupons/999")
        assert response.status_code == 404
