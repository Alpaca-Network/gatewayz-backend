"""Tests for the v3 (reward-share inference discount) half of
src/routes/delegation.py: the new public/user fields, the admin staking view
and its mutations. Money is always a decimal string; public responses carry
the disclaimer; admin routes need admin, mutations superadmin."""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import src.routes.delegation as routes
from src.main import app
from src.security.deps import get_current_user, require_admin_or_env_key, require_superadmin

client = TestClient(app)

SUPERADMIN = {"id": 1, "email": "root@example.com", "role": "superadmin"}
ENV_ADMIN = {"role": "admin", "auth": "env_key", "is_admin": True}
USER = {"id": 42, "email": "staker@example.com"}
WALLET = "0x" + "a" * 40
ADA = "stake1u" + "c" * 50
REQ = str(uuid.uuid4())

BALANCE = {
    "user_id": 42,
    "balance_usd": "12.5",
    "lifetime_granted_usd": "20",
    "lifetime_used_usd": "7.5",
    "last_grant_at": "2026-10-11T00:40:00+00:00",
}
ATTRIBUTION = {
    "id": 9,
    "asset": "eth",
    "period_key": "2026-10-10",
    "reward_date": "2026-10-10",
    "wallet_address": WALLET,
    "user_id": 42,
    "stake_native": "2.5",
    "stake_total_native": "100",
    "revenue_native": "0.01",
    "revenue_usd": "30",
    "attributed_usd": "0.75",
    "reward_share": "0.95",
    "discount_usd": "0.7125",
    "status": "granted",
    "granted_at": "2026-10-11T00:40:01+00:00",
    "created_at": "2026-10-11T00:40:00+00:00",
}
LEDGER = [
    {
        "id": 3,
        "created_at": "2026-10-11T01:00:00+00:00",
        "kind": "use",
        "amount_usd": "-1.98",
        "balance_after": "12.5",
        "request_id": REQ,
        "ref": "inference",
        "metadata": {"model": "m"},
    },
    {
        "id": 2,
        "created_at": "2026-10-11T00:59:00+00:00",
        "kind": "adjust",
        "amount_usd": "1",
        "balance_after": "14.48",
        "request_id": str(uuid.uuid4()),
        "ref": "internal note about a support ticket",
        "metadata": {},
    },
    {
        "id": 1,
        "created_at": "2026-10-11T00:40:01+00:00",
        "kind": "grant",
        "amount_usd": "0.7125",
        "balance_after": "13.48",
        "request_id": str(uuid.uuid4()),
        "ref": "delegation_share:eth:2026-10-10",
        "metadata": {},
    },
]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    saved = dict(app.dependency_overrides)
    monkeypatch.setattr(routes.Config, "DELEGATION_PAYOUT_MODEL", "reward_share")
    monkeypatch.setattr(routes.Config, "DELEGATION_REWARD_SHARE", Decimal("0.95"))
    monkeypatch.setattr(routes.Config, "DELEGATION_DISCOUNT_PERCENT", Decimal("0.99"))
    monkeypatch.setattr(routes.Config, "DELEGATION_DISCOUNT_ENABLED", False)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture
def superadmin():
    app.dependency_overrides[require_superadmin] = lambda: SUPERADMIN


@pytest.fixture
def env_admin():
    app.dependency_overrides[require_admin_or_env_key] = lambda: ENV_ADMIN


@pytest.fixture
def user():
    app.dependency_overrides[get_current_user] = lambda: USER


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(routes.Config, "DELEGATED_STAKING_ENABLED", True)
    monkeypatch.setattr(routes.Config, "STAKEWISE_VAULT_ADDRESS", None)
    monkeypatch.setattr(routes.Config, "CARDANO_POOL_ID", None)


class TestCopy:
    def test_no_forbidden_terms_in_user_facing_strings(self):
        source = Path(routes.__file__).read_text().lower()
        for phrase in ("apy", "apr", "interest", "yield", "return on your stake"):
            assert not re.search(rf"\b{phrase}\b", source), phrase

    def test_reward_share_disclaimer(self):
        assert routes.REWARD_SHARE_DISCLAIMER == (
            "Discounts are a share of staking rewards Gatewayz actually received, can change "
            "at any time, and are not a guaranteed return."
        )

    def test_allowance_model_keeps_the_v2_disclaimer(self, monkeypatch):
        monkeypatch.setattr(routes.Config, "DELEGATION_PAYOUT_MODEL", "allowance")
        data = client.get("/delegation/status").json()["data"]
        assert data["disclaimer"] == routes.DISCLAIMER
        assert data["payout_model"] == "allowance"


class TestStatus:
    def test_adds_discount_config(self, enabled, monkeypatch):
        monkeypatch.setattr(routes.Config, "DELEGATION_DISCOUNT_ENABLED", True)
        data = client.get("/delegation/status").json()["data"]
        assert data["payout_model"] == "reward_share"
        assert data["reward_share"] == "0.95"
        assert data["discount_percent"] == "0.99"
        assert data["discount_enabled"] is True
        assert data["disclaimer"] == routes.REWARD_SHARE_DISCLAIMER

    def test_discount_needs_both_flags(self, monkeypatch):
        monkeypatch.setattr(routes.Config, "DELEGATED_STAKING_ENABLED", False)
        monkeypatch.setattr(routes.Config, "DELEGATION_DISCOUNT_ENABLED", True)
        assert client.get("/delegation/status").json()["data"]["discount_enabled"] is False

    def test_bad_config_is_clamped(self, monkeypatch):
        monkeypatch.setattr(routes.Config, "DELEGATION_REWARD_SHARE", Decimal("1.7"))
        monkeypatch.setattr(routes.Config, "DELEGATION_DISCOUNT_PERCENT", Decimal("-2"))
        monkeypatch.setattr(routes.Config, "DELEGATION_PAYOUT_MODEL", "bogus")
        data = client.get("/delegation/status").json()["data"]
        assert data["reward_share"] == "1"
        assert data["discount_percent"] == "0"
        assert data["payout_model"] == "reward_share"


def _rewards(**patches):
    defaults = {
        "get_wallets_for_user": [{"wallet_address": WALLET}],
        "get_latest_measurements": [],
        "get_active_rates": {},
        "list_accruals_for_wallet": [],
        "get_balance": BALANCE,
        "list_attributions_for_account": [ATTRIBUTION],
        "list_ledger": LEDGER,
    }
    defaults.update(patches)
    mocks = {
        name: patch.object(routes, name, return_value=value) for name, value in defaults.items()
    }
    started = {name: m.start() for name, m in mocks.items()}
    try:
        response = client.get("/delegation/rewards")
    finally:
        for m in mocks.values():
            m.stop()
    return response, started


class TestRewards:
    def test_shape(self, user, enabled):
        response, _ = _rewards()
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["discount"] == {
            "enabled": False,
            "discount_percent": "0.99",
            "reward_share": "0.95",
            "balance_usd": "12.500000",
            "lifetime_granted_usd": "20.000000",
            "lifetime_used_usd": "7.500000",
        }
        assert data["attributions"] == [
            {
                "asset": "eth",
                "period_key": "2026-10-10",
                "wallet_address": WALLET,
                "stake_native": "2.500000000000000000",
                "attributed_usd": "0.750000",
                "discount_usd": "0.712500",
                "status": "granted",
                "granted_at": "2026-10-11T00:40:01+00:00",
            }
        ]
        assert [h["kind"] for h in data["discount_history"]] == ["use", "adjust", "grant"]
        assert data["discount_history"][0] == {
            "created_at": "2026-10-11T01:00:00+00:00",
            "kind": "use",
            "amount_usd": "-1.980000",
            "balance_after": "12.500000",
            "ref": "inference",
        }
        # An admin's free-text reason and billing request ids never reach users.
        assert data["discount_history"][1]["ref"] == "adjustment"
        assert data["discount_history"][2]["ref"] == "delegation_share:eth:2026-10-10"
        assert REQ not in response.text
        assert "support ticket" not in response.text
        assert data["disclaimer"] == routes.REWARD_SHARE_DISCLAIMER
        # v2 fields are kept.
        for key in ("linked_wallets", "positions", "allowance", "totals", "history"):
            assert key in data

    def test_dark_reads_nothing(self, user, monkeypatch):
        monkeypatch.setattr(routes.Config, "DELEGATED_STAKING_ENABLED", False)
        response, mocks = _rewards()
        data = response.json()["data"]
        assert data["discount"]["balance_usd"] == "0.000000"
        assert data["attributions"] == [] and data["discount_history"] == []
        for name in ("get_balance", "list_attributions_for_account", "list_ledger"):
            mocks[name].assert_not_called()

    def test_no_balance_row_is_zero(self, user, enabled):
        response, _ = _rewards(get_balance=None, list_attributions_for_account=[], list_ledger=[])
        assert response.json()["data"]["discount"]["lifetime_used_usd"] == "0.000000"


class TestAuth:
    @pytest.mark.parametrize(
        "path",
        [
            "/admin/delegation/overview",
            "/admin/delegation/accounts",
            "/admin/delegation/accounts/42",
        ],
    )
    def test_admin_reads_require_admin(self, path):
        assert client.get(path).status_code in (401, 403)

    def test_mutations_require_superadmin(self, env_admin):
        assert client.post(
            "/admin/delegation/pause", json={"asset": "eth", "reason": "x"}
        ).status_code in (401, 403)
        assert client.post(
            "/admin/delegation/accounts/42/adjust",
            json={"amount_usd": "1", "reason": "x"},
            headers={"Idempotency-Key": REQ},
        ).status_code in (401, 403)

    def test_attribute_job_requires_superadmin(self, env_admin):
        assert client.post("/admin/delegation/run", json={"job": "attribute"}).status_code in (
            401,
            403,
        )


class TestOverview:
    def test_shape(self, env_admin, enabled):
        sweeps = {
            "eth": [{"wallet_address": WALLET, "amount_raw": str(2 * 10**18), "usd_value": "5000"}],
            "ada": [],
        }
        with (
            patch.object(routes, "get_controls", return_value={"eth": {"accruals_paused": False}}),
            patch.object(
                routes,
                "list_all_wallets",
                return_value=[
                    {"wallet_address": WALLET, "user_id": 42},
                    {"wallet_address": ADA, "user_id": 43, "chain_namespace": "cip34"},
                ],
            ),
            patch.object(routes, "get_latest_sweep_rows", side_effect=lambda a: sweeps[a]),
            patch.object(
                routes,
                "sum_revenue_usd",
                side_effect=lambda a, since=None: Decimal("10") if since else Decimal("40"),
            ),
            patch.object(routes, "sum_attributed_discount", return_value=Decimal("9.5")),
            patch.object(
                routes,
                "discount_liability",
                return_value={
                    "status": "ok",
                    "discount_used_usd": "3",
                    "outstanding_balance_usd": "6.5",
                    "accounts_with_balance": 2,
                    "cost_usd": "8.9",
                },
            ),
        ):
            response = client.get("/admin/delegation/overview")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["enabled"] is True and data["payout_model"] == "reward_share"
        assert data["reward_share"] == "0.95" and data["discount_percent"] == "0.99"
        assert data["discount_enabled"] is False
        assert data["assets"]["eth"] == {
            "configured": False,
            "paused": False,
            "paused_reason": None,
            "stake_native": "2.000000000000000000",
            "stake_usd": "5000.000000",
            "linked_wallets": 1,
            "revenue_usd_30d": "10.000000",
            "revenue_usd_total": "40.000000",
            "discount_granted_usd_total": "9.500000",
        }
        # ADA has no controls row -> paused (fail closed), and no sweep yet.
        assert data["assets"]["ada"]["paused"] is True
        assert data["assets"]["ada"]["stake_native"] == "0.000000"
        assert data["assets"]["ada"]["linked_wallets"] == 1
        assert data["discount_used_usd_total"] == "3.000000"
        assert data["outstanding_balance_usd"] == "6.500000"
        assert data["accounts_with_balance"] == 2

    def test_unreadable_totals_are_null_not_zero(self, env_admin):
        with (
            patch.object(routes, "get_controls", return_value=None),
            patch.object(routes, "list_all_wallets", return_value=[]),
            patch.object(routes, "get_latest_sweep_rows", return_value=None),
            patch.object(routes, "sum_revenue_usd", return_value=None),
            patch.object(routes, "sum_attributed_discount", return_value=None),
            patch.object(routes, "discount_liability", return_value={"status": "unknown"}),
        ):
            data = client.get("/admin/delegation/overview").json()["data"]
        eth = data["assets"]["eth"]
        assert eth["paused"] is True
        assert eth["stake_native"] is None and eth["revenue_usd_total"] is None
        assert eth["discount_granted_usd_total"] is None
        assert data["outstanding_balance_usd"] is None


class TestAccounts:
    def _patches(self):
        return (
            patch.object(
                routes,
                "get_latest_sweep_rows",
                side_effect=lambda a: (
                    [{"wallet_address": WALLET, "amount_raw": str(10**18), "usd_value": "2500"}]
                    if a == "eth"
                    else [{"wallet_address": ADA, "amount_raw": "0", "usd_value": "0"}]
                ),
            ),
            patch.object(
                routes,
                "list_all_wallets",
                return_value=[
                    {"wallet_address": WALLET, "user_id": 42},
                    {"wallet_address": ADA, "user_id": 44, "chain_namespace": "cip34"},
                ],
            ),
            patch.object(
                routes,
                "list_balances",
                return_value=[BALANCE, {"user_id": 43, "balance_usd": "50"}],
            ),
            patch.object(routes, "list_attribution_user_ids", return_value={42, 45}),
            patch.object(
                routes,
                "get_user_emails",
                return_value={42: "staker@example.com", 43: "big@example.com", 45: None},
            ),
        )

    def _get(self, url):
        p = self._patches()
        for m in p:
            m.start()
        try:
            return client.get(url)
        finally:
            for m in p:
                m.stop()

    def test_lists_balances_attributions_and_stakers_largest_first(self, env_admin):
        data = self._get("/admin/delegation/accounts").json()["data"]
        # 44 only has a zero-stake wallet -> not listed.
        assert [i["user_id"] for i in data["items"]] == [43, 42, 45]
        assert data["total"] == 3
        item = data["items"][1]
        assert item == {
            "user_id": 42,
            "email": "staker@example.com",
            "wallets": [
                {
                    "address": WALLET,
                    "asset": "eth",
                    "stake_native": "1.000000000000000000",
                    "stake_usd": "2500.000000",
                }
            ],
            "balance_usd": "12.500000",
            "lifetime_granted_usd": "20.000000",
            "lifetime_used_usd": "7.500000",
            "last_grant_at": "2026-10-11T00:40:00+00:00",
        }

    @pytest.mark.parametrize(
        "q,expected", [("42", [42]), ("BIG@", [43]), (WALLET[-6:], [42]), ("nobody", [])]
    )
    def test_search(self, env_admin, q, expected):
        data = self._get(f"/admin/delegation/accounts?q={q}").json()["data"]
        assert [i["user_id"] for i in data["items"]] == expected

    def test_paging(self, env_admin):
        data = self._get("/admin/delegation/accounts?limit=1&offset=1").json()["data"]
        assert [i["user_id"] for i in data["items"]] == [42] and data["total"] == 3

    def test_limit_is_bounded(self, env_admin):
        assert client.get("/admin/delegation/accounts?limit=1000").status_code == 422

    def test_detail(self, env_admin):
        with (
            patch.object(routes, "get_user_by_id", return_value={"id": 42, "email": "s@x"}),
            patch.object(routes, "get_latest_sweep_rows", return_value=[]),
            patch.object(routes, "get_wallets_for_user", return_value=[{"wallet_address": WALLET}]),
            patch.object(routes, "get_balance", return_value=BALANCE),
            patch.object(routes, "list_attributions_for_account", return_value=[ATTRIBUTION]),
            patch.object(routes, "list_ledger", return_value=LEDGER),
        ):
            data = client.get("/admin/delegation/accounts/42").json()["data"]
        assert data["account"]["user_id"] == 42 and data["account"]["balance_usd"] == "12.500000"
        assert data["attributions"][0]["revenue_usd"] == "30.000000"
        assert data["attributions"][0]["reward_share"] == "0.95"
        # Admins see the raw ref, request id and metadata.
        assert data["discount_history"][1]["ref"] == "internal note about a support ticket"
        assert data["discount_history"][0]["request_id"] == REQ

    def test_detail_unknown_user_404(self, env_admin):
        with patch.object(routes, "get_user_by_id", return_value=None):
            assert client.get("/admin/delegation/accounts/99").status_code == 404


class TestAdjust:
    def _post(self, body, key=REQ, result=None):
        headers = {"Idempotency-Key": key} if key is not None else {}
        with (
            patch.object(routes, "adjust_discount", return_value=result) as adjust,
            patch.object(routes, "record_audit") as audit,
        ):
            response = client.post(
                "/admin/delegation/accounts/42/adjust", json=body, headers=headers
            )
        return response, adjust, audit

    def test_adjusts_and_audits(self, superadmin):
        response, adjust, audit = self._post(
            {"amount_usd": "-1.5", "reason": "duplicate grant"},
            result={"status": "adjusted", "amount_usd": "-1.5", "balance_after": "11"},
        )
        assert response.status_code == 200
        assert response.json()["data"] == {
            "user_id": 42,
            "status": "adjusted",
            "amount_usd": "-1.500000",
            "balance_after": "11.000000",
            "request_id": REQ,
        }
        adjust.assert_called_once_with(42, Decimal("-1.5"), REQ, "duplicate grant", 1)
        assert audit.call_args.kwargs["action"] == "delegation.discount.adjust"

    def test_non_uuid_key_maps_to_a_stable_per_account_uuid(self, superadmin):
        _, a1, _ = self._post(
            {"amount_usd": "1", "reason": "x"}, key="click-1", result={"status": "exists"}
        )
        _, a2, _ = self._post(
            {"amount_usd": "1", "reason": "x"}, key="click-1", result={"status": "exists"}
        )
        rid = a1.call_args.args[2]
        assert uuid.UUID(rid) and rid == a2.call_args.args[2]

    def test_replay_is_not_audited_twice(self, superadmin):
        _, _, audit = self._post(
            {"amount_usd": "1", "reason": "x"},
            result={"status": "exists", "amount_usd": "1", "balance_after": "2"},
        )
        audit.assert_not_called()

    def test_requires_idempotency_key(self, superadmin):
        response, adjust, _ = self._post({"amount_usd": "1", "reason": "x"}, key=None)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "idempotency_key_required"
        adjust.assert_not_called()

    @pytest.mark.parametrize(
        "body",
        [
            {"amount_usd": "1"},
            {"amount_usd": "1", "reason": "   "},
            {"amount_usd": "0", "reason": "x"},
            {"amount_usd": "0.0000001", "reason": "x"},
            {"amount_usd": "1e9", "reason": "x"},
            {"amount_usd": "NaN", "reason": "x"},
        ],
    )
    def test_validation(self, superadmin, body):
        response, adjust, _ = self._post(body)
        assert response.status_code == 422
        adjust.assert_not_called()

    @pytest.mark.parametrize(
        "status,code,error",
        [
            ("insufficient_balance", 422, "would_go_negative"),
            ("user_not_found", 404, "user_not_found"),
            ("invalid", 422, "invalid_adjustment"),
            (None, 500, "adjust_failed"),
        ],
    )
    def test_refusals(self, superadmin, status, code, error):
        response, _, audit = self._post(
            {"amount_usd": "-100", "reason": "x"}, result={"status": status} if status else None
        )
        assert response.status_code == code
        assert response.json()["error"]["code"] == error
        audit.assert_not_called()


class TestPause:
    def test_pauses_and_audits(self, superadmin):
        with (
            patch.object(routes, "pause_accruals", return_value=True) as pause,
            patch.object(routes, "record_audit") as audit,
        ):
            response = client.post(
                "/admin/delegation/pause", json={"asset": "ada", "reason": "pool retiring"}
            )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["asset"] == "ada" and data["paused"] is True
        assert "pool retiring" in data["paused_reason"]
        assert pause.call_args.args[0] == "ada"
        assert audit.call_args.kwargs["action"] == "delegation.pause"

    def test_requires_reason_and_known_asset(self, superadmin):
        with patch.object(routes, "pause_accruals") as pause:
            assert client.post("/admin/delegation/pause", json={"asset": "eth"}).status_code == 422
            assert (
                client.post(
                    "/admin/delegation/pause", json={"asset": "btc", "reason": "x"}
                ).status_code
                == 422
            )
        pause.assert_not_called()

    def test_failure_is_500(self, superadmin):
        with patch.object(routes, "pause_accruals", return_value=False):
            response = client.post("/admin/delegation/pause", json={"asset": "eth", "reason": "x"})
        assert response.status_code == 500


def test_run_attribute_dispatches_and_audits(superadmin):
    with (
        patch.object(routes, "run_delegation_attribution_once", return_value={"ok": 1}) as fn,
        patch.object(routes, "record_audit") as audit,
    ):
        response = client.post("/admin/delegation/run", json={"job": "attribute"})
    assert response.status_code == 200
    fn.assert_called_once_with(None)
    assert audit.call_args.kwargs["action"] == "delegation.run.attribute"
