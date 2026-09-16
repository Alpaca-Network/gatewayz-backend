"""Guard: no embedded `models(...)` select may reference a column `models` lacks.

Sibling of test_users_table_columns.py, for the *embedded resource* half of the
same defect. PostgREST embeds a joined table as `models!inner(col, ...)`, and a
stale name inside those parentheses fails the whole query with 42703 --
`column models_1.model_id does not exist` -- exactly like a stale name on the
outer table.

This bit twice. `public.models` identifies a model with `provider_model_id`;
there is no `models.model_id`. The confusing part is that
`chat_completion_requests.model_id` *is* real, so the name looks correct at a
glance and only the embedded copy is wrong:

    .select("model_id, models!inner(model_id, model_name))")
             ^^^^^^^^ real           ^^^^^^^^ phantom -> 42703

It cost `/admin/monitoring/chat-requests`, `/plot-data` and the model list a
500 each, and made `/chat-requests/models` fall through to a fallback that
issued one HTTP request per model (6,427 for a single provider) -- a timeout
that read as "monitoring is slow" rather than "this query is invalid".
"""

from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"

# public.models as it exists in production.
# Keep in sync with supabase/migrations/ when a column is added or dropped.
MODELS_COLUMNS = frozenset(
    {
        "average_response_time_ms",
        "canonical_id",
        "capabilities",
        "categories",
        "consecutive_missing_count",
        "content_hash",
        "context_length",
        "created_at",
        "deprecated_at",
        "description",
        "has_json_mode",
        "health_status",
        "id",
        "is_active",
        "is_free",
        "is_reasoning",
        "last_health_check_at",
        "last_seen_in_provider_at",
        "latency_tier",
        "max_output_tokens",
        "metadata",
        "modality",
        "model_name",
        "pricing_format_migrated",
        "pricing_original_completion",
        "pricing_original_image",
        "pricing_original_prompt",
        "pricing_original_request",
        "provider_id",
        "provider_model_id",
        "success_rate",
        "supports_function_calling",
        "supports_streaming",
        "supports_vision",
        "updated_at",
    }
)

# Real hits this guard found that are NOT safe to fix mechanically. Each must
# name the file:line and say why, so the list cannot quietly become a mute button.
KNOWN_EXCEPTIONS = {
    # backfill_missing_costs() selects models(pricing_prompt, pricing_completion);
    # neither exists, so the backfill has been failing with 42703 and no historic
    # cost has ever been written. The apparent fix -- pricing_original_prompt /
    # _completion -- is NOT safe: those columns hold inconsistent units across rows
    # (see src/services/model_offers_projection.py:14 and normalized_cost_per_1k),
    # while this code multiplies raw tokens as if the value were per-token. Renaming
    # would replace a clean failure with silently wrong dollar amounts written to
    # chat_completion_requests.cost_usd. Needs the unit question settled first.
    ("src/db/chat_completion_requests.py", "pricing_prompt"),
    ("src/db/chat_completion_requests.py", "pricing_completion"),
}

# `models!inner(...)` / `models(...)` / `models!left(...)`, capturing the body.
EMBED_RE = re.compile(r"\bmodels(?:!\w+)?\(([^()]*(?:\([^()]*\)[^()]*)*)\)")

# Nested embeds (providers!inner(...)) are checked against their own table, not
# this one, so strip them before splitting the column list.
NESTED_RE = re.compile(r"\b\w+(?:!\w+)?\([^()]*\)")


def _embedded_columns(body: str) -> list[str]:
    body = NESTED_RE.sub("", body)
    cols = []
    for raw in body.split(","):
        col = raw.strip().strip("\"'")
        # Strip a PostgREST rename alias (`alias:column`) down to the column.
        if ":" in col:
            col = col.split(":", 1)[1].strip()
        if col and col != "*":
            cols.append(col)
    return cols


def test_no_embedded_models_select_names_a_missing_column():
    violations = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in EMBED_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            for col in _embedded_columns(match.group(1)):
                if col in MODELS_COLUMNS:
                    continue
                if (str(path.relative_to(SRC.parent)), col) in KNOWN_EXCEPTIONS:
                    continue
                violations.append(
                    f"{path.relative_to(SRC.parent)}:{line} embeds models.{col}, "
                    f"which does not exist on public.models"
                )
    assert not violations, "Embedded models(...) selects reference missing columns:\n" + "\n".join(
        violations
    )


def test_guard_catches_a_planted_phantom_column():
    """The scanner must fail on the exact string that caused the outage."""
    planted = '.select("model_id, models!inner(id, model_id, model_name))")'
    bad = [
        c for c in _embedded_columns(EMBED_RE.search(planted).group(1)) if c not in MODELS_COLUMNS
    ]
    assert bad == ["model_id"], f"scanner missed the planted phantom column: {bad}"


def test_guard_does_not_flag_the_outer_model_id_or_nested_embeds():
    """`chat_completion_requests.model_id` is real, and providers(...) is another table."""
    good = '.select("model_id, models!inner(id, model_name, providers!inner(name, slug)))")'
    cols = _embedded_columns(EMBED_RE.search(good).group(1))
    assert [c for c in cols if c not in MODELS_COLUMNS] == []
    assert (
        "name" not in cols and "slug" not in cols
    ), "nested providers columns leaked into the check"
