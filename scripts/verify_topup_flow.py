#!/usr/bin/env python3
"""Verify the credit top-up flow end to end (GTM plan section 1, gate 3).

The gate is "a card to usable credits in under two minutes, no sales call, no
approval queue". Most of that is verifiable without a human: the only part that
genuinely needs a person is typing card details into Stripe's hosted page.

This script checks everything either side of that:

1. A checkout session can be created, and the minimum amount is what we tell
   users it is.
2. The success-page status endpoint reconciles a paid session, so a late
   webhook cannot strand a paying user.
3. Credits actually appear on the balance, and how long that took.
4. A live key can be minted once credits exist (the payment gate opens).

Run against Stripe **test mode** with a test card. With
``--session-id`` it verifies a session you completed manually in a browser;
without it, it verifies everything up to the hosted page and tells you what to
do next.

    export GATEWAYZ_API_URL=https://api.gatewayz.ai
    export GATEWAYZ_API_KEY=...
    python scripts/verify_topup_flow.py --amount 500
    # complete checkout in the browser, then:
    python scripts/verify_topup_flow.py --session-id cs_test_...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field

import httpx

API_URL = os.getenv("GATEWAYZ_API_URL", "https://api.gatewayz.ai")

# The gate from the GTM plan.
TARGET_SECONDS = 120


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    seconds: float | None = None


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str, seconds: float | None = None) -> None:
        self.checks.append(Check(name, passed, detail, seconds))
        icon = "PASS" if passed else "FAIL"
        timing = f" ({seconds:.2f}s)" if seconds is not None else ""
        print(f"[{icon}]{timing} {name}: {detail}")

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)


def _client(api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=API_URL.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=30.0,
    )


def get_balance(client: httpx.Client) -> float | None:
    try:
        resp = client.get("/user/balance")
        resp.raise_for_status()
        data = resp.json()
        for key in ("credits", "balance", "current_balance"):
            if key in data:
                return float(data[key])
        return None
    except Exception:
        return None


def check_minimum_matches_what_we_advertise(client: httpx.Client, report: Report) -> None:
    """The gate message and the checkout validator must agree.

    A gate that says "top up $1" in front of a checkout that rejects $1 is a
    dead end at the exact moment the user was willing to pay.
    """
    advertised = float(os.getenv("MIN_TOPUP_USD", "5.0"))
    below = int(advertised * 100) - 1

    resp = client.post(
        "/api/stripe/checkout-session",
        json={"amount": below, "currency": "usd"},
    )
    rejected = resp.status_code in (400, 422)

    resp_at = client.post(
        "/api/stripe/checkout-session",
        json={"amount": int(advertised * 100), "currency": "usd"},
    )
    accepted = resp_at.status_code < 400

    report.add(
        "minimum top-up is consistent",
        rejected and accepted,
        f"${advertised:.2f} accepted={accepted}, ${below / 100:.2f} rejected={rejected}",
    )


def check_checkout_creation(client: httpx.Client, amount_cents: int, report: Report) -> str | None:
    start = time.perf_counter()
    resp = client.post(
        "/api/stripe/checkout-session",
        json={"amount": amount_cents, "currency": "usd"},
    )
    elapsed = time.perf_counter() - start

    if resp.status_code >= 400:
        report.add(
            "checkout session created", False, f"HTTP {resp.status_code}: {resp.text[:200]}", elapsed
        )
        return None

    data = resp.json()
    url = data.get("url") or data.get("checkout_url")
    session_id = data.get("session_id") or data.get("id")

    report.add(
        "checkout session created",
        bool(url and session_id),
        f"session={session_id}",
        elapsed,
    )
    if url:
        print(f"\n  Complete checkout here with a Stripe test card (4242 4242 4242 4242):\n  {url}\n")
    return session_id


def check_session_reconciles(client: httpx.Client, session_id: str, report: Report) -> None:
    """The success page must grant credits itself if the webhook is late."""
    start = time.perf_counter()
    resp = client.get(f"/api/stripe/checkout-session/{session_id}")
    elapsed = time.perf_counter() - start

    if resp.status_code >= 400:
        report.add("session status readable", False, f"HTTP {resp.status_code}", elapsed)
        return

    data = resp.json()
    paid = data.get("payment_status") == "paid"
    report.add("session is paid", paid, f"payment_status={data.get('payment_status')}", elapsed)

    if not paid:
        return

    # credits_reconciled is True only when this call is what granted the
    # credits, i.e. the webhook had not landed. Either value is a pass; the
    # field being absent means the reconciliation path is not deployed.
    if "credits_reconciled" not in data:
        report.add(
            "late-webhook reconciliation present",
            False,
            "response has no credits_reconciled field — a dropped webhook would "
            "strand a paying user on the success page",
        )
    else:
        report.add(
            "late-webhook reconciliation present",
            True,
            f"credits_reconciled={data['credits_reconciled']}",
        )


def check_credits_landed(
    client: httpx.Client, before: float | None, amount_cents: int, report: Report
) -> None:
    if before is None:
        report.add("credits landed", False, "could not read balance before payment")
        return

    start = time.perf_counter()
    deadline = start + TARGET_SECONDS
    expected = before + (amount_cents / 100)

    while time.perf_counter() < deadline:
        current = get_balance(client)
        if current is not None and current >= expected - 0.01:
            elapsed = time.perf_counter() - start
            report.add(
                "credits landed",
                True,
                f"balance {before:.2f} -> {current:.2f}",
                elapsed,
            )
            report.add(
                "under the 2-minute gate",
                elapsed < TARGET_SECONDS,
                f"{elapsed:.1f}s vs {TARGET_SECONDS}s target",
            )
            return
        time.sleep(2)

    report.add(
        "credits landed",
        False,
        f"balance still {get_balance(client)} after {TARGET_SECONDS}s (expected {expected:.2f})",
    )


def check_live_key_unlocked(client: httpx.Client, report: Report) -> None:
    """Once credits exist, the payment gate must open."""
    resp = client.post(
        "/user/api-keys",
        json={
            "action": "create",
            "key_name": f"topup-verify-{int(time.time())}",
            "environment_tag": "live",
        },
    )
    ok = resp.status_code < 400
    report.add(
        "live key unlocked after payment",
        ok,
        f"HTTP {resp.status_code}"
        + ("" if ok else f": {resp.text[:200]}"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--amount", type=int, default=500, help="Top-up amount in cents")
    parser.add_argument(
        "--session-id", help="Verify a session already completed in the browser"
    )
    parser.add_argument("--json", dest="json_out", help="Write the report here")
    args = parser.parse_args()

    api_key = os.getenv("GATEWAYZ_API_KEY")
    if not api_key:
        print("GATEWAYZ_API_KEY is not set", file=sys.stderr)
        return 2

    report = Report()
    client = _client(api_key)

    try:
        before = get_balance(client)
        print(f"Starting balance: {before}\n")

        if args.session_id:
            check_session_reconciles(client, args.session_id, report)
            check_credits_landed(client, before, args.amount, report)
            check_live_key_unlocked(client, report)
        else:
            check_minimum_matches_what_we_advertise(client, report)
            session_id = check_checkout_creation(client, args.amount, report)
            if session_id:
                print(
                    "Complete the checkout above, then re-run with:\n"
                    f"  python scripts/verify_topup_flow.py --session-id {session_id} "
                    f"--amount {args.amount}\n"
                )
    finally:
        client.close()

    payload = {
        "api_url": API_URL,
        "checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail, "seconds": c.seconds}
            for c in report.checks
        ],
        "all_passed": report.ok,
    }

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    print(f"\n{'ALL CHECKS PASSED' if report.ok else 'SOME CHECKS FAILED'}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
