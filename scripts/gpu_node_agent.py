#!/usr/bin/env python3
"""GPU node agent for Gatewayz community compute (gatewayz-backend#2267;
design doc scratchpad/m4/spec.md section 8, "GPU Transparency Dashboard +
Compute Marketplace").

Runs on a provider's machine, next to a local OpenAI-compatible server
(vLLM first). Every `--interval` seconds it:

1. Self-checks the local server (`GET {local-vllm}/v1/models`) to get the
   list of model ids it's actually serving, and best-effort reads
   `outstanding` load from vLLM's `/metrics` Prometheus endpoint.
2. POSTs a heartbeat to the gateway, authenticated with the node's bearer
   token (`gw_node_...`, issued once at `POST /gpu/nodes` registration --
   see docs/gpu/PROVIDER_ONBOARDING.md). If `--wallet-keyfile` is given,
   the heartbeat is additionally signed with the provider's payout wallet
   key, which the gateway marks `attested_heartbeat=true`.

Optionally (`--attest-proxy PORT`) it also runs a tiny reverse proxy in
front of the local vLLM server that adds an `X-Gatewayz-Attestation`
header (a wallet signature covering the request/response) to every
response -- this lets the gateway's spot-check verifier trust a node's
own claim about what it served without re-running every request. This is
recommended, not required, at testnet stage.

Dependencies: stdlib + httpx (already in requirements.txt) + eth_account
(installed transitively via web3, already in requirements.txt) -- eth_account
is only imported when `--wallet-keyfile`/`--attest-proxy` need it, so the
agent runs on a bare node with neither.

CROSS-REPO CONTRACT (see docs/api.md "GPU Marketplace" and docs/gpu/
attestation.md): the community adapter (`src/services/providers/
community_adapter.py`) forwards the request's billing_ref to the node as
an inbound `X-Gatewayz-Request-Id` header on the outbound call it makes --
a second, distinct use of the header name `RequestIDMiddleware` also
echoes back to the client on the *response* (see
src/middleware/request_id_middleware.py). Without it, `--attest-proxy`
cannot attribute a response to a billing_ref and skips attestation for
that request (passes it through unmodified) rather than guessing.

CROSS-REPO CONTRACT (canonicalisation): the exact byte-for-byte hashing
rule below (`hash_prompt`/`hash_response`) matches
`src/services/gpu/hashing.py` (verified in
tests/scripts/test_gpu_node_agent.py by importing and comparing against
the real backend module, including a non-ASCII fixture -- both sides rely
on `json.dumps`' default `ensure_ascii=True`). If either side's
canonicalisation ever drifts, every node's attestation signatures
silently stop verifying -- that test is the guard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("gpu_node_agent")

DEFAULT_INTERVAL_SECONDS = 30
MAX_BACKOFF_SECONDS = 300
HEARTBEAT_MESSAGE_PREFIX = "gatewayz-heartbeat"
BILLING_REF_HEADER = "X-Gatewayz-Request-Id"
ATTESTATION_HEADER = "X-Gatewayz-Attestation"
_SSE_CONTENT_TYPE = "text/event-stream"

# vLLM's Prometheus metric names for in-flight requests (both counted --
# "waiting" is queued but not yet dispatched, "running" is actively serving).
_VLLM_RUNNING_METRIC = "vllm:num_requests_running"
_VLLM_WAITING_METRIC = "vllm:num_requests_waiting"


# ---------------------------------------------------------------------------
# Attestation hashing -- MUST match src/services/gpu/hashing.py (see module
# docstring's cross-repo contract note).
# ---------------------------------------------------------------------------


def _canonical_json(obj: Any) -> str:
    """Sorted-key, whitespace-free JSON -- the same bytes for the same
    logical content regardless of client-side key/whitespace ordering.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def hash_prompt(messages: list[dict]) -> str:
    """sha256 hex digest of the canonical JSON of the request's `messages`."""
    return hashlib.sha256(_canonical_json(messages).encode("utf-8")).hexdigest()


def hash_response(response_text: str) -> str:
    """sha256 hex digest of the raw response text (concatenated assistant
    message content, not re-encoded as JSON -- the response IS text).
    """
    return hashlib.sha256(response_text.encode("utf-8")).hexdigest()


def _sig_hex(signature_bytes_hex: str) -> str:
    """eth_account's `SignedMessage.signature.hex()` drops the `0x` prefix
    on some installed versions -- the exact bug that shipped three Critical
    bugs in this project before (see src/security/wallet_signature.py's
    docstring and tests/security/test_wallet_signature.py's `_sign` helper,
    which this mirrors). Always normalize before sending/comparing.
    """
    return (
        signature_bytes_hex if signature_bytes_hex.startswith("0x") else f"0x{signature_bytes_hex}"
    )


def sign_message(account, message: str) -> str:
    """Sign `message` with `account` (an eth_account.Account) the same way
    src/security/wallet_signature.py verifies it: `encode_defunct(text=...)`
    then `Account.recover_message`. Imports eth_account lazily so the agent
    runs without it when no signing is requested.
    """
    from eth_account.messages import encode_defunct

    signed = account.sign_message(encode_defunct(text=message))
    return _sig_hex(signed.signature.hex())


def load_wallet_account(keyfile: Path):
    """Load a payout wallet's private key from a local file (one line, hex,
    `0x`-prefixed or not -- eth_account.Account.from_key accepts both).
    Never logs the key. The file is the operator's responsibility to keep
    off any shared machine and out of version control (`chmod 600` --
    warned below on POSIX if that's not already the case).
    """
    from eth_account import Account

    if os.name == "posix":
        mode = keyfile.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            logger.warning(
                "%s is readable by group/other -- run `chmod 600 %s` "
                "(this file holds your payout wallet's private key)",
                keyfile,
                keyfile,
            )

    key = keyfile.read_text().strip()
    return Account.from_key(key)


# ---------------------------------------------------------------------------
# Heartbeat loop
# ---------------------------------------------------------------------------


def _parse_vllm_outstanding(metrics_text: str) -> int:
    """Best-effort parse of vLLM's `/metrics` Prometheus text exposition.
    Returns 0 (not an error) if the metrics endpoint is missing, disabled,
    or doesn't expose these gauges -- outstanding is advisory load info for
    the gateway's routing, not something a heartbeat should fail over.
    """
    running = waiting = 0.0
    for line in metrics_text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, value = line.rpartition(" ")
        if name == _VLLM_RUNNING_METRIC:
            running = float(value)
        elif name == _VLLM_WAITING_METRIC:
            waiting = float(value)
    return int(running + waiting)


def probe_local_server(client: httpx.Client, local_url: str) -> tuple[list[str], int]:
    """Return (model ids, outstanding request count) from the local
    OpenAI-compatible server. Raises httpx.HTTPError if `/v1/models` itself
    is unreachable -- that's a real heartbeat failure (the node has nothing
    to serve), unlike a missing `/metrics` (best-effort only).
    """
    base = local_url.rstrip("/")
    resp = client.get(f"{base}/v1/models", timeout=5.0)
    resp.raise_for_status()
    model_ids = [m["id"] for m in resp.json().get("data", [])]

    outstanding = 0
    try:
        metrics_resp = client.get(f"{base}/metrics", timeout=5.0)
        if metrics_resp.status_code == 200:
            outstanding = _parse_vllm_outstanding(metrics_resp.text)
    except httpx.HTTPError:
        logger.debug("no /metrics endpoint on local server; reporting outstanding=0")

    return model_ids, outstanding


def build_heartbeat_payload(
    *,
    models: list[str],
    outstanding: int,
    node_id: str,
    version: str | None = None,
    account=None,
    gpu_util_pct: float | None = None,
    now: float | None = None,
) -> dict:
    """Build the heartbeat POST body per spec section 3:
    `{load: {outstanding, gpu_util_pct?}, models:[…], version?, signature?}`.

    `signature` shape is a design decision this workstream made because the
    spec names the signed message (`f"gatewayz-heartbeat:{node_id}:{ts}"`)
    but not the wire shape carrying it -- the verifier needs the exact `ts`
    used, so it travels alongside the signature rather than being
    re-derived server-side from request time (clock skew/latency would
    otherwise break verification): `{"ts": <unix seconds>, "value": "0x..."}`.
    `src/routes/gpu.py`'s `node_heartbeat` handler verifies against exactly
    this shape (`HeartbeatSignature`, within a 300s skew window).
    """
    load: dict[str, Any] = {"outstanding": outstanding}
    if gpu_util_pct is not None:
        load["gpu_util_pct"] = gpu_util_pct

    payload: dict[str, Any] = {"load": load, "models": models}
    if version:
        payload["version"] = version

    if account is not None:
        ts = int(now if now is not None else time.time())
        message = f"{HEARTBEAT_MESSAGE_PREFIX}:{node_id}:{ts}"
        payload["signature"] = {"ts": ts, "value": sign_message(account, message)}

    return payload


def send_heartbeat(
    client: httpx.Client, gateway: str, node_id: str, node_token: str, payload: dict
) -> dict:
    url = f"{gateway.rstrip('/')}/gpu/nodes/{node_id}/heartbeat"
    resp = client.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {node_token}"},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def next_backoff(current: int, base: int, cap: int = MAX_BACKOFF_SECONDS) -> int:
    """Exponential backoff, capped, reset to `base` on the next success by
    the caller (see run_loop) rather than here -- this function is pure so
    it's directly testable without a real clock or sleeps.
    """
    return min(max(current, base) * 2, cap)


def heartbeat_once(client: httpx.Client, args: argparse.Namespace, account) -> dict:
    """One probe+heartbeat cycle. Raises httpx.HTTPError on failure --
    callers decide whether that's fatal (`--once`) or retryable (the loop).
    """
    models, outstanding = probe_local_server(client, args.local_vllm)
    payload = build_heartbeat_payload(
        models=models,
        outstanding=outstanding,
        node_id=args.node_id,
        version=args.version,
        account=account,
    )
    result = send_heartbeat(client, args.gateway, args.node_id, args.node_token, payload)
    logger.info("heartbeat ok: %d model(s), %d outstanding", len(models), outstanding)
    return result


def run_loop(client: httpx.Client, args: argparse.Namespace, account) -> None:
    if args.once:
        heartbeat_once(client, args, account)
        return

    backoff = args.interval
    while True:
        try:
            heartbeat_once(client, args, account)
            backoff = args.interval
        except httpx.HTTPError as e:
            logger.warning("heartbeat failed: %s -- retrying in %ds", e, backoff)
            time.sleep(backoff)
            backoff = next_backoff(backoff, args.interval)
            continue
        time.sleep(args.interval)


# ---------------------------------------------------------------------------
# Optional attestation reverse proxy
# ---------------------------------------------------------------------------


def build_attestation(
    request_body: bytes, response_body: bytes, billing_ref: str, account
) -> str | None:
    """Return the `X-Gatewayz-Attestation` header value for one exchange, or
    None when this wasn't a chat-completions-shaped JSON exchange (e.g. the
    client hit `GET /v1/models` through the proxy) -- attestation only
    makes sense for inference calls, and skipping silently (rather than
    raising) keeps the proxy transparent for everything else.

    v1 limitation: non-streaming only. A streaming response's body isn't
    a single JSON document to hash; `_AttestProxyHandler` never calls this
    for a streaming exchange in the first place (see `_relay_streaming`) --
    the proxy still forwards it, unbuffered, just without an attestation
    header. Documented in docs/gpu/PROVIDER_ONBOARDING.md.
    """
    try:
        req_json = json.loads(request_body)
        resp_json = json.loads(response_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    messages = req_json.get("messages")
    if messages is None:
        return None

    model = resp_json.get("model") or req_json.get("model", "")
    usage = resp_json.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    choices = resp_json.get("choices") or []
    # n > 1: only choices[0] counts -- must match community_adapter.py's
    # _record_receipt, which never hashes any choice past the first (see
    # docs/gpu/attestation.md). Concatenating every choice would hash
    # something the gateway's own receipt was never computed against.
    first_choice = choices[0] if choices else {}
    response_text = (first_choice.get("message") or {}).get("content") or ""

    prompt_hash = hash_prompt(messages)
    response_hash = hash_response(response_text)
    message = (
        f"{billing_ref}|{model}|{prompt_hash}|{response_hash}|{prompt_tokens}|{completion_tokens}"
    )
    return sign_message(account, message)


def _request_wants_streaming(body: bytes) -> bool:
    """True iff the inbound request body is JSON with `"stream": true`.
    Used alongside the upstream response's Content-Type (see `_forward`)
    to decide whether to relay chunk-by-chunk or buffer.
    """
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return bool(isinstance(parsed, dict) and parsed.get("stream"))


class _AttestProxyHandler(BaseHTTPRequestHandler):
    # Set per-instance by make_attest_proxy_handler() below. `client` is an
    # httpx.Client rather than the module-level httpx.stream()/httpx.request()
    # shortcuts specifically so tests can inject one built with
    # httpx.MockTransport (see tests/scripts/test_gpu_node_agent.py) instead
    # of hitting a real upstream.
    upstream: str
    account: Any
    client: httpx.Client

    def log_message(self, fmt: str, *fmt_args) -> None:  # noqa: D401 -- BaseHTTPRequestHandler hook
        logger.debug("attest-proxy: " + fmt, *fmt_args)

    def do_GET(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        billing_ref = self.headers.get(BILLING_REF_HEADER)

        forward_headers = {
            k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")
        }
        upstream_url = f"{self.upstream.rstrip('/')}{self.path}"

        # client.stream() connects and reads headers eagerly but the body
        # lazily -- this lets us decide streaming vs. buffered (by the
        # request's own `stream: true`, or the response's actual
        # Content-Type, whichever signals it first) before committing to
        # either path, without ever fully reading a long SSE body upfront.
        try:
            with self.client.stream(
                self.command, upstream_url, content=body, headers=forward_headers, timeout=60.0
            ) as resp:
                is_streaming = _request_wants_streaming(
                    body
                ) or _SSE_CONTENT_TYPE in resp.headers.get("content-type", "")
                if is_streaming:
                    self._relay_streaming(resp)
                else:
                    resp.read()
                    self._relay_buffered(resp, body, billing_ref)
        except httpx.HTTPError as e:
            logger.warning("attest-proxy: upstream request failed: %s", e)
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(e).encode("utf-8"))

    def _relay_streaming(self, resp: httpx.Response) -> None:
        """Copy a streamed (SSE) response through chunk by chunk, in real
        time, instead of buffering the whole thing -- vLLM's token-by-token
        delivery would otherwise be lost and a long completion could
        exceed `_forward`'s request timeout for no reason (a real streaming
        client wouldn't need to). Attestation is skipped here: headers must
        be sent before the body starts, and there's no single response
        document to hash yet anyway (documented limitation, see
        docs/gpu/PROVIDER_ONBOARDING.md).
        """
        self.send_response(resp.status_code)
        for k, v in resp.headers.items():
            if k.lower() in ("content-length", "transfer-encoding", "connection"):
                continue
            self.send_header(k, v)
        self.end_headers()
        try:
            for chunk in resp.iter_bytes():
                if chunk:
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (httpx.HTTPError, BrokenPipeError, ConnectionError) as e:
            # Headers are already on the wire -- there's no way to turn this
            # into a clean error response now, just stop and log.
            logger.warning("attest-proxy: streaming relay interrupted: %s", e)

    def _relay_buffered(
        self, resp: httpx.Response, request_body: bytes, billing_ref: str | None
    ) -> None:
        """Buffer-then-send path for ordinary (non-streaming) JSON
        responses -- small enough to hold in memory, and buffering is what
        lets `build_attestation` hash the complete response.
        """
        attestation = None
        if billing_ref:
            attestation = build_attestation(request_body, resp.content, billing_ref, self.account)
        elif resp.status_code < 400:
            logger.debug(
                "attest-proxy: no %s on inbound request; forwarding without attestation "
                "(gateway must forward this header -- see PROVIDER_ONBOARDING.md)",
                BILLING_REF_HEADER,
            )

        self.send_response(resp.status_code)
        for k, v in resp.headers.items():
            if k.lower() in ("content-length", "transfer-encoding", "connection"):
                continue
            self.send_header(k, v)
        if attestation:
            self.send_header(ATTESTATION_HEADER, attestation)
        self.send_header("Content-Length", str(len(resp.content)))
        self.end_headers()
        self.wfile.write(resp.content)


def make_attest_proxy_handler(
    upstream: str, account, client: httpx.Client | None = None
) -> type[_AttestProxyHandler]:
    return type(
        "BoundAttestProxyHandler",
        (_AttestProxyHandler,),
        {"upstream": upstream, "account": account, "client": client or httpx.Client()},
    )


def start_attest_proxy(
    port: int, upstream: str, account, client: httpx.Client | None = None
) -> ThreadingHTTPServer:
    if account is None:
        raise ValueError("--attest-proxy requires --wallet-keyfile (attestation must be signed)")
    server = ThreadingHTTPServer(
        ("127.0.0.1", port), make_attest_proxy_handler(upstream, account, client)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="gpu-attest-proxy")
    thread.start()
    logger.info("attest-proxy listening on 127.0.0.1:%d -> %s", port, upstream)
    return server


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--gateway", required=True, help="Gatewayz base URL, e.g. https://api.gatewayz.ai"
    )
    parser.add_argument(
        "--node-token", required=True, help="gw_node_... bearer token from node registration"
    )
    parser.add_argument("--node-id", required=True, help="Node id returned at registration")
    parser.add_argument(
        "--local-vllm", default="http://127.0.0.1:8000", help="Local OpenAI-compat server URL"
    )
    parser.add_argument(
        "--wallet-keyfile",
        help="Path to a file holding the payout wallet's private key (hex, one line). "
        "Enables signed heartbeats and, with --attest-proxy, response attestation.",
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="Heartbeat interval, seconds"
    )
    parser.add_argument(
        "--once", action="store_true", help="Run a single heartbeat cycle and exit (for tests)"
    )
    parser.add_argument(
        "--attest-proxy",
        type=int,
        metavar="PORT",
        help="Run a local reverse proxy on 127.0.0.1:PORT in front of --local-vllm that adds "
        "X-Gatewayz-Attestation to responses. Requires --wallet-keyfile.",
    )
    parser.add_argument(
        "--version", help="Agent/node software version string, reported in heartbeats"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    account = load_wallet_account(Path(args.wallet_keyfile)) if args.wallet_keyfile else None

    proxy_server = None
    if args.attest_proxy:
        proxy_server = start_attest_proxy(args.attest_proxy, args.local_vllm, account)

    client = httpx.Client()
    try:
        run_loop(client, args, account)
    except KeyboardInterrupt:
        logger.info("shutting down")
    except httpx.HTTPError as e:
        logger.error("fatal heartbeat error: %s", e)
        sys.exit(1)
    finally:
        client.close()
        if proxy_server is not None:
            proxy_server.shutdown()


if __name__ == "__main__":
    main()
