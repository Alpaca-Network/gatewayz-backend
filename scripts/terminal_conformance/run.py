#!/usr/bin/env python3
"""Terminal conformance: can a model run a real Claude Code session on Gatewayz?

For each model, drives the real Claude Code client (``claude -p``) against
Gatewayz's ``/v1/messages`` in a throwaway folder and checks the outcome on
disk and in the answer, not just the HTTP status:

  tool_loop       read a file, fix a bug in another, run it
  parallel_tools  read three files in one turn
  large_file      read a ~60k-token file and quote a line near its end
  subagent        delegate a file read to a sub-agent

A model is "agent-ready" when tool_loop, parallel_tools and large_file pass.
A failed scenario is run once more before it counts as failed.

Usage:
  GATEWAYZ_API_KEY=... python scripts/terminal_conformance/run.py \\
      --models anthropic/claude-haiku-4-5-20251001 moonshot/kimi-k2.6 \\
      [--scenarios tool_loop parallel_tools] [--out results.json]

Needs Node and the ``claude`` command on PATH. Uses only the standard library.
The key is passed to the client through its environment and never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_BASE_URL = "https://api.gatewayz.ai"
AGENT_READY_SCENARIOS = ("tool_loop", "parallel_tools", "large_file")
DEFAULT_MODELS = (
    "anthropic/claude-sonnet-5-5",
    "anthropic/claude-haiku-4-5-20251001",
    "openai/gpt-5.5",
    "openai/gpt-5.4-mini",
    "grok-4",
    "moonshot/kimi-k2.6",
    "deepseek-ai/DeepSeek-V4.1-Flash",
    "deepseek-ai/DeepSeek-V4-Pro",
    "Qwen/Qwen3-Coder-480B-A35B-Instruct-Turbo",
    "zai-org/GLM-5.3",
)


def _fixtures(folder: Path) -> None:
    (folder / "a.txt").write_text("alpha: the launch code is MAPLE-41\n")
    (folder / "b.txt").write_text("beta: the backup code is CEDAR-77\n")
    (folder / "c.txt").write_text("gamma: the archive code is BIRCH-09\n")
    (folder / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n\n\nassert add(2, 3) == 5, 'add is wrong'\n"
    )
    rng = random.Random(7)
    words = ["harbor", "ledger", "copper", "meadow", "signal", "timber", "orchard", "granite"]
    lines = [" ".join(rng.choice(words) for _ in range(12)) for _ in range(5200)]
    lines[5100] = "the vault code is WILLOW-58"
    (folder / "big.txt").write_text("\n".join(lines) + "\n")


SCENARIOS = {
    "tool_loop": {
        "prompt": (
            "Read a.txt to find the launch code. Then fix the bug in calc.py so add returns "
            "the sum, and run python3 calc.py to confirm it exits without errors. In your "
            "final answer, state the launch code and what you changed."
        ),
        "max_turns": 10,
        "tools": ["Read", "Edit", "Write", "Bash(python3 calc.py)"],
        "check": lambda folder, answer: (
            "MAPLE-41" in answer and "return a + b" in (folder / "calc.py").read_text()
        ),
    },
    "parallel_tools": {
        "prompt": (
            "Read a.txt, b.txt and c.txt in a single turn using three parallel tool calls, "
            "then list the three codes."
        ),
        "max_turns": 6,
        "tools": ["Read"],
        "check": lambda folder, answer: all(
            code in answer for code in ("MAPLE-41", "CEDAR-77", "BIRCH-09")
        ),
    },
    "large_file": {
        "prompt": (
            "Read the whole of big.txt (it is long; read all of it, in several reads if "
            "needed) and tell me the vault code that appears near the end."
        ),
        "max_turns": 12,
        "tools": ["Read", "Grep"],
        "check": lambda folder, answer: "WILLOW-58" in answer,
    },
    "subagent": {
        "prompt": (
            "Use a sub-agent (the Task or Agent tool) to read b.txt and report the backup "
            "code back to you. Then tell me the code."
        ),
        "max_turns": 8,
        "tools": ["Read", "Task", "Agent"],
        "check": lambda folder, answer: "CEDAR-77" in answer,
    },
}


def _client_env(model: str, key: str, base_url: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_"))}
    env.update(
        ANTHROPIC_BASE_URL=base_url,
        ANTHROPIC_AUTH_TOKEN=key,
        ANTHROPIC_MODEL=model,
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        API_TIMEOUT_MS="300000",
    )
    if "claude" in model.lower():
        env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = "anthropic/claude-haiku-4-5-20251001"
        env["ANTHROPIC_CUSTOM_HEADERS"] = "x-gatewayz-passthrough: true"
    else:
        env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = model
        env["CLAUDE_CODE_SUBAGENT_MODEL"] = model
        env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "1"
    return env


def run_scenario(model: str, name: str, key: str, base_url: str, timeout: int) -> dict:
    spec = SCENARIOS[name]
    folder = Path(tempfile.mkdtemp(prefix=f"gz-conformance-{name}-"))
    started = time.monotonic()
    result: dict = {"scenario": name, "passed": False}
    try:
        _fixtures(folder)
        cmd = [
            "claude",
            "-p",
            spec["prompt"],
            "--model",
            model,
            "--max-turns",
            str(spec["max_turns"]),
            "--output-format",
            "json",
            "--permission-mode",
            "acceptEdits",
            "--strict-mcp-config",
            "--allowedTools",
            *spec["tools"],
        ]
        proc = subprocess.run(
            cmd,
            cwd=folder,
            env=_client_env(model, key, base_url),
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        try:
            out = json.loads(proc.stdout)
        except ValueError:
            out = {}
        answer = str(out.get("result") or "")
        passed = bool(not out.get("is_error")) and bool(spec["check"](folder, answer))
        result.update(
            passed=passed,
            client_error=bool(out.get("is_error")),
            turns=out.get("num_turns"),
            # The answer (or the client's error text) when the check failed;
            # the key never appears in either.
            detail=None if passed else answer[:300],
        )
        if not out:
            result["detail"] = (proc.stderr or proc.stdout)[-300:]
    except subprocess.TimeoutExpired:
        result["detail"] = f"timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001 - report, never crash the whole run
        result["detail"] = f"runner error: {exc!r}"[:300]
    finally:
        result["seconds"] = round(time.monotonic() - started, 1)
        shutil.rmtree(folder, ignore_errors=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--scenarios", nargs="+", default=list(SCENARIOS), choices=list(SCENARIOS))
    parser.add_argument("--base-url", default=os.environ.get("GATEWAYZ_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--timeout", type=int, default=420, help="seconds per scenario")
    parser.add_argument("--out", default="terminal-conformance.json")
    args = parser.parse_args()

    key = os.environ.get("GATEWAYZ_API_KEY")
    if not key:
        print("GATEWAYZ_API_KEY is not set", file=sys.stderr)
        return 2
    if not shutil.which("claude"):
        print("the claude command is not on PATH", file=sys.stderr)
        return 2

    report = {"base_url": args.base_url, "client": _client_version(), "models": []}
    for model in args.models:
        scenarios = []
        for name in args.scenarios:
            # One retry: model output varies run to run (Kimi failed the
            # parallel-tools check once and passed it on the next run).
            outcome = run_scenario(model, name, key, args.base_url, args.timeout)
            if not outcome["passed"]:
                retry = run_scenario(model, name, key, args.base_url, args.timeout)
                retry["first_attempt"] = outcome
                outcome = retry
            scenarios.append(outcome)
        passed = {s["scenario"] for s in scenarios if s["passed"]}
        ready = all(name in passed for name in AGENT_READY_SCENARIOS if name in args.scenarios)
        report["models"].append({"model": model, "agent_ready": ready, "scenarios": scenarios})
        marks = " ".join(f"{s['scenario']}={'pass' if s['passed'] else 'FAIL'}" for s in scenarios)
        print(f"{'READY' if ready else 'NOT READY':9}  {model:48}  {marks}", flush=True)

    Path(args.out).write_text(json.dumps(report, indent=2))
    ready = sum(m["agent_ready"] for m in report["models"])
    print(f"\n{ready} of {len(report['models'])} models agent-ready. Details: {args.out}")
    return 0


def _client_version() -> str:
    try:
        return subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


if __name__ == "__main__":
    sys.exit(main())
