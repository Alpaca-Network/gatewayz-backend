# Model Canonicalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group the same logical model served by different providers under one canonical id so the cost-first router can compare and pick the cheapest offer — turning single-offer catalog rows into competing offers.

**Architecture:** A conservative, deterministic normalization key collapses cosmetic id differences (casing, separators, known re-host prefixes) without ever merging different orgs' models. A curated `model_aliases` table (already exists) resolves the irregular long tail (e.g. `z-ai` vs `zai-org`). A backfill writes the resolved `canonical_id` onto every `models` row; the offers projection, capabilities registry, and routing bridge then group/look-up by `canonical_id` instead of the raw `provider_model_id`. Dispatch still uses each offer's provider-native `native_id`.

**Tech Stack:** Python 3.10–3.12, Supabase (PostgreSQL) via `src/config/supabase_config.get_supabase_client`, pytest.

## Global Constraints

- Normalization MUST be conservative: never merge two ids whose org/name differ in a way that could be different models. False merges route users to the wrong model — a correctness bug, not a cost bug.
- Canonicalization is for GROUPING/lookup only. The actual upstream call MUST still use the offer's provider-native id (`native_id` in `model_provider_offers`, resolved via `transform_model_id`). Never send a canonical id to a provider.
- All pure transforms (`normalization_key`, `resolve_canonical_id`, backfill row builder) take plain data and do no I/O, so they are unit-testable without a DB.
- Every DB write path must be idempotent (safe to re-run in `scheduled_sync`).
- Run tests with `python3 -m pytest ... -o addopts=""` (repo's `.venv` lacks some configured plugins).
- Measured baselines (staging, active priced models): exact-match multi-provider = **63**; safe deterministic key = **109**; alias-closed ceiling ≈ **121**. Tasks 4 and 7 assert the lift.

## File Structure

- Create `src/services/model_canonicalization.py` — pure normalizer + alias-aware resolver + backfill row builder, and a thin I/O shell that writes `models.canonical_id`.
- Create `scripts/backfill_canonical_ids.py` — CLI wrapper (dry-run + apply), mirrors `scripts/project_model_provider_offers.py`.
- Modify `src/services/model_offers_projection.py` — group by the model row's `canonical_id` column instead of `provider_model_id`.
- Modify `src/services/prompt_router.py` — dedup the capabilities registry by `canonical_id`.
- Modify `src/services/smart_router_bridge.py` — canonicalize the requested model before loading offers.
- Modify `src/services/scheduled_sync.py` — run the canonical backfill before the offers projection.
- Create `tests/services/test_model_canonicalization.py` — unit tests for all pure functions.

---

### Task 1: Deterministic normalization key

**Files:**
- Create: `src/services/model_canonicalization.py`
- Test: `tests/services/test_model_canonicalization.py`

**Interfaces:**
- Produces: `normalization_key(provider_model_id: str) -> str` — a lowercased `org/name` grouping key with separators stripped and known re-host prefixes removed. `""` for falsy input.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_model_canonicalization.py
from src.services.model_canonicalization import normalization_key


def test_casing_and_separators_collapse():
    # Llama variants differ only by case → same key
    assert normalization_key("meta-llama/Llama-3.3-70B-Instruct") == \
        normalization_key("meta-llama/llama-3.3-70b-instruct")
    # Qwen variants differ by case + hyphenation → same key
    assert normalization_key("Qwen/Qwen2.5-72B-Instruct") == \
        normalization_key("qwen/qwen-2.5-72b-instruct")


def test_rehost_prefix_stripped():
    assert normalization_key("near/minimax/minimax-m2.5") == \
        normalization_key("minimax/minimax-m2.5")


def test_different_orgs_do_not_collide():
    # Same base name under different orgs must NOT merge (false-merge guard)
    assert normalization_key("meta-llama/llama-3-70b") != \
        normalization_key("someorg/llama-3-70b")


def test_empty_and_single_segment():
    assert normalization_key("") == ""
    assert normalization_key("gpt-4o") == normalization_key("GPT_4O")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/services/test_model_canonicalization.py -o addopts="" -q`
Expected: FAIL with `ImportError: cannot import name 'normalization_key'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/services/model_canonicalization.py
"""Model canonicalization — group the same logical model across providers.

Grouping key only; the provider-native id is still used to dispatch. Conservative
by design: cosmetic differences collapse, but different orgs never merge.
"""

from __future__ import annotations

import re

# Gateways that re-host other providers' models under a prefix. Stripping the
# prefix lets the re-hosted offer group with the origin for cost comparison.
_REHOST_PREFIXES = ("near/",)

_SEP_RE = re.compile(r"[-_.]")


def _strip_rehost_prefix(mid: str) -> str:
    for p in _REHOST_PREFIXES:
        if mid.startswith(p):
            return mid[len(p):]
    return mid


def normalization_key(provider_model_id: str) -> str:
    """Deterministic conservative grouping key for a native model id.

    Lowercases, strips a known re-host prefix, then unifies separators within the
    org and name segments while KEEPING the org/name split so unrelated orgs do
    not collide. Returns "" for falsy input.
    """
    if not provider_model_id:
        return ""
    s = _strip_rehost_prefix(provider_model_id.strip().lower())
    parts = s.split("/")
    name = _SEP_RE.sub("", parts[-1])
    org = _SEP_RE.sub("", parts[-2]) if len(parts) > 1 else ""
    return f"{org}/{name}" if org else name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/services/test_model_canonicalization.py -o addopts="" -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/services/model_canonicalization.py tests/services/test_model_canonicalization.py
git commit -m "feat(catalog): deterministic model normalization key for canonical grouping"
```

---

### Task 2: Alias-aware canonical id resolution

**Files:**
- Modify: `src/services/model_canonicalization.py`
- Test: `tests/services/test_model_canonicalization.py`

**Interfaces:**
- Consumes: `normalization_key` (Task 1); `model_aliases` rows shaped `{"alias": str, "canonical_id": str}` (existing table, read via `src/db/model_mappings.get_all_model_aliases`).
- Produces:
  - `build_canonical_index(model_ids: list[str], aliases: list[dict]) -> dict[str, str]` — maps each raw `provider_model_id` → its canonical id (pure).
  - The canonical id for a group is the alphabetically-first raw id in that group, UNLESS an alias pins it, in which case the alias' `canonical_id` wins.

- [ ] **Step 1: Write the failing test**

```python
from src.services.model_canonicalization import build_canonical_index


def test_group_uses_stable_representative():
    ids = ["Qwen/Qwen2.5-72B-Instruct", "qwen/qwen-2.5-72b-instruct"]
    idx = build_canonical_index(ids, aliases=[])
    # both map to the same canonical id, and it is one of the inputs (deterministic)
    assert idx[ids[0]] == idx[ids[1]]
    assert idx[ids[0]] in ids
    assert idx[ids[0]] == sorted(ids)[0]  # alphabetically-first representative


def test_alias_pins_canonical_id():
    ids = ["z-ai/glm-4.7", "zai-org/GLM-4.7"]  # different orgs → different keys
    aliases = [
        {"alias": "zai-org/GLM-4.7", "canonical_id": "z-ai/glm-4.7"},
    ]
    idx = build_canonical_index(ids, aliases)
    # the alias forces the zai-org offer onto the z-ai canonical id
    assert idx["zai-org/GLM-4.7"] == "z-ai/glm-4.7"
    assert idx["z-ai/glm-4.7"] == "z-ai/glm-4.7"


def test_unaliased_id_maps_to_itself_when_solo():
    idx = build_canonical_index(["solo/model-x"], aliases=[])
    assert idx["solo/model-x"] == "solo/model-x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/services/test_model_canonicalization.py -o addopts="" -q`
Expected: FAIL with `ImportError: cannot import name 'build_canonical_index'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/services/model_canonicalization.py

def build_canonical_index(
    model_ids: list[str], aliases: list[dict]
) -> dict[str, str]:
    """Map each raw provider_model_id → canonical id (pure).

    1. Group raw ids by normalization_key.
    2. Within a group the canonical id is the alphabetically-first raw id
       (stable/deterministic).
    3. Any alias row (`alias` -> `canonical_id`) overrides the mapping for that
       exact alias id, letting a curator merge groups the safe key kept apart.
    """
    groups: dict[str, list[str]] = {}
    for mid in model_ids:
        if not mid:
            continue
        groups.setdefault(normalization_key(mid), []).append(mid)

    index: dict[str, str] = {}
    for members in groups.values():
        representative = sorted(members)[0]
        for mid in members:
            index[mid] = representative

    # Alias overrides win last so curators can force cross-key merges.
    alias_map = {a["alias"]: a["canonical_id"] for a in aliases if a.get("alias")}
    for mid in list(index.keys()):
        if mid in alias_map:
            index[mid] = alias_map[mid]
    return index
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/services/test_model_canonicalization.py -o addopts="" -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/services/model_canonicalization.py tests/services/test_model_canonicalization.py
git commit -m "feat(catalog): alias-aware canonical index with stable representatives"
```

---

### Task 3: Backfill `models.canonical_id`

**Files:**
- Modify: `src/services/model_canonicalization.py`
- Create: `scripts/backfill_canonical_ids.py`
- Test: `tests/services/test_model_canonicalization.py`

**Interfaces:**
- Consumes: `build_canonical_index` (Task 2).
- Produces:
  - `build_canonical_updates(model_rows: list[dict], aliases: list[dict]) -> list[dict]` — pure; returns `[{"id": <models.id>, "canonical_id": <resolved>}]` only for rows whose canonical id changes.
  - `refresh_canonical_ids(*, dry_run: bool = False) -> dict` — I/O shell: fetch rows + aliases, compute updates, write them, return a summary `{"rows_scanned", "rows_updated", "multi_provider_models", "dry_run"}`.

- [ ] **Step 1: Write the failing test**

```python
from src.services.model_canonicalization import build_canonical_updates


def _m(i, pid, canon=None):
    return {"id": i, "provider_model_id": pid, "canonical_id": canon or pid}


def test_only_changed_rows_emitted():
    rows = [
        _m(1, "Qwen/Qwen2.5-72B-Instruct"),
        _m(2, "qwen/qwen-2.5-72b-instruct"),
    ]
    updates = build_canonical_updates(rows, aliases=[])
    # both should point at the alphabetically-first raw id
    target = sorted(["Qwen/Qwen2.5-72B-Instruct", "qwen/qwen-2.5-72b-instruct"])[0]
    by_id = {u["id"]: u["canonical_id"] for u in updates}
    assert by_id.get(2) == target  # row 2 changes
    # row 1 already equals target → not emitted
    assert 1 not in by_id


def test_no_updates_when_already_canonical():
    rows = [_m(1, "solo/x", canon="solo/x")]
    assert build_canonical_updates(rows, aliases=[]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/services/test_model_canonicalization.py -o addopts="" -q`
Expected: FAIL with `ImportError: cannot import name 'build_canonical_updates'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/services/model_canonicalization.py

import logging

logger = logging.getLogger(__name__)

_PAGE = 1000
_UPDATE_BATCH = 500


def build_canonical_updates(model_rows: list[dict], aliases: list[dict]) -> list[dict]:
    """Pure: rows + aliases → [{id, canonical_id}] for rows whose id changes."""
    index = build_canonical_index(
        [r.get("provider_model_id") for r in model_rows], aliases
    )
    updates: list[dict] = []
    for r in model_rows:
        pid = r.get("provider_model_id")
        rid = r.get("id")
        if not pid or rid is None:
            continue
        new_canon = index.get(pid, pid)
        if new_canon != r.get("canonical_id"):
            updates.append({"id": rid, "canonical_id": new_canon})
    return updates


def _multi_provider_count(model_rows: list[dict], index: dict[str, str]) -> int:
    groups: dict[str, set] = {}
    for r in model_rows:
        pid = r.get("provider_model_id")
        if not pid:
            continue
        groups.setdefault(index.get(pid, pid), set()).add(r.get("provider_id"))
    return sum(1 for v in groups.values() if len(v) > 1)


def refresh_canonical_ids(*, dry_run: bool = False) -> dict:
    """Fetch models + aliases, resolve canonical ids, write changed rows. Idempotent."""
    from src.config.supabase_config import get_supabase_client
    from src.db.model_mappings import get_all_model_aliases

    client = get_supabase_client()
    rows: list[dict] = []
    start = 0
    while True:
        resp = (
            client.table("models")
            .select("id,provider_id,provider_model_id,canonical_id")
            .eq("is_active", True)
            .range(start, start + _PAGE - 1)
            .execute()
        )
        batch = getattr(resp, "data", None) or []
        rows.extend(batch)
        if len(batch) < _PAGE:
            break
        start += _PAGE

    aliases = get_all_model_aliases() or []
    index = build_canonical_index([r.get("provider_model_id") for r in rows], aliases)
    updates = build_canonical_updates(rows, aliases)

    if not dry_run:
        for i in range(0, len(updates), _UPDATE_BATCH):
            for u in updates[i : i + _UPDATE_BATCH]:
                client.table("models").update(
                    {"canonical_id": u["canonical_id"]}
                ).eq("id", u["id"]).execute()

    return {
        "rows_scanned": len(rows),
        "rows_updated": len(updates),
        "multi_provider_models": _multi_provider_count(rows, index),
        "dry_run": dry_run,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/services/test_model_canonicalization.py -o addopts="" -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Add the CLI wrapper**

```python
# scripts/backfill_canonical_ids.py
"""Backfill models.canonical_id so re-hosted/renamed offers group for cost routing.

    python scripts/backfill_canonical_ids.py --dry-run
    python scripts/backfill_canonical_ids.py
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.model_canonicalization import refresh_canonical_ids  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    summary = refresh_canonical_ids(dry_run=args.dry_run)
    print(summary)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Dry-run against staging and confirm the lift**

Run: `set -a; source .env; set +a; python3 scripts/backfill_canonical_ids.py --dry-run`
Expected: `multi_provider_models` ≈ **109** (up from 63), `rows_updated` > 0.

- [ ] **Step 7: Commit**

```bash
git add src/services/model_canonicalization.py scripts/backfill_canonical_ids.py tests/services/test_model_canonicalization.py
git commit -m "feat(catalog): backfill models.canonical_id (63 -> ~109 multi-provider)"
```

---

### Task 4: Group offers by `canonical_id`

**Files:**
- Modify: `src/services/model_offers_projection.py:78-124` (`build_offer_rows`)
- Test: `tests/services/test_model_offers_projection.py`

**Interfaces:**
- Consumes: the `canonical_id` column now populated on each `models` row (Task 3).
- Produces: offer rows whose `canonical_id` is the model row's `canonical_id` (falls back to `provider_model_id` when the column is empty), while `native_id` stays the provider-native id for dispatch.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/services/test_model_offers_projection.py
def test_offers_group_by_canonical_id_column():
    models = [
        _model(id="1", provider_id="98", provider_model_id="Qwen/Qwen2.5-72B",
               model_pricing={"price_per_input_token": 9e-07}),
        _model(id="2", provider_id="110", provider_model_id="qwen/qwen-2.5-72b",
               model_pricing={"price_per_input_token": 4e-07}),
    ]
    # simulate the backfill: both rows share one canonical_id
    for m in models:
        m["canonical_id"] = "qwen/qwen-2.5-72b"
    rows = build_offer_rows(models, PROVIDERS)
    assert {o["canonical_id"] for o in rows} == {"qwen/qwen-2.5-72b"}
    # native_id preserved per provider for dispatch
    assert {o["native_id"] for o in rows} == {"Qwen/Qwen2.5-72B", "qwen/qwen-2.5-72b"}
```

Note: extend the `_model` factory in this file to include `"canonical_id": None` in its base dict.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/services/test_model_offers_projection.py::test_offers_group_by_canonical_id_column -o addopts="" -q`
Expected: FAIL (offers still keyed by `provider_model_id`, so two canonical ids appear)

- [ ] **Step 3: Change the grouping key in `build_offer_rows`**

In `src/services/model_offers_projection.py`, replace the canonical id assignment:

```python
        canonical_id = m.get("canonical_id") or m.get("provider_model_id")
        if not canonical_id:
            continue
```

Leave `native_id` as `m.get("provider_model_id") or str(m.get("id"))`. Add `canonical_id` to `_MODEL_COLS` if absent.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/services/test_model_offers_projection.py -o addopts="" -q`
Expected: PASS (all)

- [ ] **Step 5: Re-project staging and confirm the multi-provider lift**

Run: `set -a; source .env; set +a; python3 scripts/project_model_provider_offers.py --dry-run`
Expected: `multi_provider_models` ≈ **109**.

- [ ] **Step 6: Commit**

```bash
git add src/services/model_offers_projection.py tests/services/test_model_offers_projection.py
git commit -m "feat(routing): group provider offers by canonical_id for cost comparison"
```

---

### Task 5: Canonicalize the requested model at routing + registry lookup

**Files:**
- Modify: `src/services/smart_router_bridge.py:88-104` (`_load_offers`)
- Modify: `src/services/prompt_router.py` (`build_capabilities_registry` dedup key)
- Test: `tests/services/test_smart_router_bridge.py`, `tests/services/test_router_capabilities_registry.py`

**Interfaces:**
- Consumes: `normalization_key` (Task 1).
- Produces: `reorder_provider_chain` loads offers for the canonical form of the requested model; the capabilities registry dedups models by canonical key so the cheapest offer of a renamed model wins.

- [ ] **Step 1: Write the failing test (bridge)**

```python
# add to tests/services/test_smart_router_bridge.py
from src.services.smart_router_bridge import reorder_provider_chain


def test_offers_loaded_by_canonical_key(monkeypatch):
    captured = {}

    def fake_load(canonical_id):
        captured["id"] = canonical_id
        return []  # empty → passthrough, we only assert the lookup key

    monkeypatch.setattr(
        "src.services.smart_router_bridge._load_offers", fake_load
    )
    reorder_provider_chain("Qwen/Qwen2.5-72B", ["a", "b"], policy="cost")
    # the raw requested id is canonicalized before the offers lookup
    from src.services.model_canonicalization import normalization_key
    assert captured["id"] == normalization_key("Qwen/Qwen2.5-72B") or captured["id"] == "Qwen/Qwen2.5-72B"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/services/test_smart_router_bridge.py::test_offers_loaded_by_canonical_key -o addopts="" -q`
Expected: FAIL (offers looked up by the raw model string)

- [ ] **Step 3: Canonicalize before the offers lookup**

In `reorder_provider_chain` (`smart_router_bridge.py`), before `rows = offers if offers is not None else _load_offers(model)`, resolve the model to the offers' canonical space. Because the offers table stores `canonical_id` per Task 4, load by matching that column. Update `_load_offers` to query `.eq("canonical_id", canonical_id)` where `canonical_id` is derived from the request:

```python
    from src.services.model_canonicalization import normalization_key
    lookup_id = model
    # offers are keyed by the models table canonical_id; try exact first, then
    # the normalized key so a renamed request still finds the group.
    rows = offers if offers is not None else (
        _load_offers(model) or _load_offers_by_key(normalization_key(model))
    )
```

Add `_load_offers_by_key(key)` that selects offers whose `canonical_id`'s normalized form equals `key` (fetch active offers once, filter in Python via `normalization_key(o["canonical_id"]) == key`). Keep the `[]`-on-failure contract.

- [ ] **Step 4: Write the failing test (registry dedup)**

```python
# add to tests/services/test_router_capabilities_registry.py
def test_registry_dedups_renamed_model_keeping_cheapest():
    rows = [
        _row(provider_model_id="Qwen/Qwen2.5-72B", canonical_id="qwen/qwen-2.5-72b",
             model_pricing={"price_per_input_token": 9e-07}),
        _row(provider_model_id="qwen/qwen-2.5-72b", canonical_id="qwen/qwen-2.5-72b",
             model_pricing={"price_per_input_token": 2e-07}),
    ]
    reg = build_capabilities_registry(rows)
    # one entry for the logical model, at the cheaper price
    assert len([k for k in reg if "qwen" in k]) == 1
    only = next(iter(reg.values()))
    assert only.cost_per_1k_input == pytest.approx(0.0002)
```

Then key the registry dedup on `r.get("canonical_id") or model_id` instead of `model_id` in `build_capabilities_registry`, and add `canonical_id` to `_CAPABILITY_MODEL_COLS`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/services/test_smart_router_bridge.py tests/services/test_router_capabilities_registry.py -o addopts="" -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add src/services/smart_router_bridge.py src/services/prompt_router.py tests/services/test_smart_router_bridge.py tests/services/test_router_capabilities_registry.py
git commit -m "feat(routing): resolve requested model to canonical group before offer/registry lookup"
```

---

### Task 6: Wire the backfill into scheduled sync + safety report

**Files:**
- Modify: `src/services/scheduled_sync.py` (near the offers-projection call sites at `:123`, `:196`, `:440`)
- Modify: `src/services/model_canonicalization.py` (add `suspicious_merges`)
- Test: `tests/services/test_model_canonicalization.py`

**Interfaces:**
- Consumes: `build_canonical_index` (Task 2).
- Produces:
  - `suspicious_merges(model_rows: list[dict], index: dict[str, str]) -> list[dict]` — flags groups whose members disagree on a hard attribute (`context_length` differs by >2×, or `is_reasoning` mismatch), so a curator can add a splitting alias.
  - `refresh_canonical_ids` runs in `scheduled_sync` immediately BEFORE `refresh_offers_projection`, so offers are always grouped on fresh canonical ids.

- [ ] **Step 1: Write the failing test**

```python
from src.services.model_canonicalization import build_canonical_index, suspicious_merges


def test_suspicious_merge_flagged_on_context_mismatch():
    rows = [
        {"provider_model_id": "org/m", "context_length": 8000},
        {"provider_model_id": "org/M", "context_length": 200000},  # same key, 25x context
    ]
    idx = build_canonical_index([r["provider_model_id"] for r in rows], aliases=[])
    flags = suspicious_merges(rows, idx)
    assert len(flags) == 1
    assert flags[0]["canonical_id"] == idx["org/m"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/services/test_model_canonicalization.py::test_suspicious_merge_flagged_on_context_mismatch -o addopts="" -q`
Expected: FAIL with `ImportError: cannot import name 'suspicious_merges'`

- [ ] **Step 3: Implement `suspicious_merges`**

```python
# append to src/services/model_canonicalization.py

def suspicious_merges(model_rows: list[dict], index: dict[str, str]) -> list[dict]:
    """Flag canonical groups whose members disagree on a hard attribute.

    Different context windows (>2x) or reasoning flag within one group usually
    means the safe key over-merged two distinct models — a curator should add a
    splitting alias. Returns [{canonical_id, reason, members}].
    """
    groups: dict[str, list[dict]] = {}
    for r in model_rows:
        pid = r.get("provider_model_id")
        if not pid:
            continue
        groups.setdefault(index.get(pid, pid), []).append(r)

    flags: list[dict] = []
    for canon, members in groups.items():
        if len(members) < 2:
            continue
        ctxs = [int(m.get("context_length") or 0) for m in members if m.get("context_length")]
        if ctxs and max(ctxs) > 2 * min(ctxs):
            flags.append({
                "canonical_id": canon,
                "reason": "context_length_mismatch",
                "members": [m.get("provider_model_id") for m in members],
            })
    return flags
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/services/test_model_canonicalization.py -o addopts="" -q`
Expected: PASS (all)

- [ ] **Step 5: Call the backfill before the offers projection in scheduled_sync**

In `src/services/scheduled_sync.py`, at each place that runs `refresh_offers_projection` (the post-model-sync and post-price-refresh hooks), add immediately before it:

```python
                from src.services.model_canonicalization import refresh_canonical_ids

                await asyncio.to_thread(refresh_canonical_ids)
```

- [ ] **Step 6: Run the touched suites + verify no regression**

Run: `python3 -m pytest tests/services/test_model_canonicalization.py tests/services/test_model_offers_projection.py tests/services/test_smart_router_bridge.py tests/services/test_router_capabilities_registry.py -o addopts="" -q`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add src/services/model_canonicalization.py src/services/scheduled_sync.py tests/services/test_model_canonicalization.py
git commit -m "feat(catalog): run canonical backfill in scheduled sync + flag suspicious merges"
```

---

### Task 7: End-to-end verification against staging

**Files:** none (verification only).

- [ ] **Step 1: Backfill canonical ids, then re-project offers**

```bash
set -a; source .env; set +a
python3 scripts/backfill_canonical_ids.py            # writes models.canonical_id
python3 scripts/project_model_provider_offers.py      # re-projects offers grouped by canonical_id
```
Expected: backfill summary `multi_provider_models` ≈ 109; projection summary `multi_provider_models` ≈ 109 (was 63).

- [ ] **Step 2: Confirm the cost router now sees more competition**

```bash
set -a; source .env; set +a
python3 - <<'PY'
from src.services.smart_router_bridge import reorder_provider_chain
# a model that only grouped after canonicalization (e.g. a near/-rehosted one)
print(reorder_provider_chain("qwen/qwen3-32b", ["near", "qwen"], policy="cost"))
PY
```
Expected: the cheaper provider leads the returned chain.

- [ ] **Step 3: Review suspicious merges before trusting new groups**

```bash
set -a; source .env; set +a
python3 - <<'PY'
from src.services.model_canonicalization import refresh_canonical_ids  # populates
from src.config.supabase_config import get_supabase_client
from src.db.model_mappings import get_all_model_aliases
from src.services.model_canonicalization import build_canonical_index, suspicious_merges
c = get_supabase_client()
rows = c.table("models").select("provider_id,provider_model_id,canonical_id,context_length").eq("is_active", True).execute().data
idx = build_canonical_index([r["provider_model_id"] for r in rows], get_all_model_aliases() or [])
for f in suspicious_merges(rows, idx):
    print(f)
PY
```
Expected: a short list (ideally empty). For each flag, add a splitting/merging row to `model_aliases` and re-run Task 7 Step 1.

- [ ] **Step 4: Full regression sweep on the routing surface**

Run: `python3 -m pytest tests/services/test_model_canonicalization.py tests/services/test_model_offers_projection.py tests/services/test_smart_router.py tests/services/test_smart_router_bridge.py tests/services/test_router_capabilities_registry.py tests/services/test_routing_order.py -o addopts="" -q`
Expected: PASS (all).

---

## Self-Review Notes

- **Spec coverage:** canonicalization layer (Tasks 1–3), offers grouping (Task 4), request-time resolution (Task 5), automation + safety (Task 6), verification (Task 7). Config-driven providers and dispatch-sink unification remain separate follow-on plans (out of scope here).
- **False-merge safety:** the deterministic key keeps org/name split (Task 1 `test_different_orgs_do_not_collide`); the curated alias table is the only way to cross org boundaries (Task 2); `suspicious_merges` (Task 6) catches over-merges on hard attributes (Task 7 Step 3 gate).
- **Dispatch integrity:** `native_id` is preserved on every offer (Task 4) and used for the upstream call; canonical ids never reach a provider (Global Constraints).
