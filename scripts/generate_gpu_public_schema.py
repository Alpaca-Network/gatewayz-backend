#!/usr/bin/env python3
"""Regenerate docs/gpu/public-feed.schema.json from the live Pydantic models
in src/schemas/gpu_public.py (gatewayz-backend#2263 #2264).

Run this after changing any model in src/schemas/gpu_public.py:

    python scripts/generate_gpu_public_schema.py

tests/routes/test_gpu_public.py::test_schema_matches_committed_file fails
the build if the committed file and the live schema ever drift apart.
"""

import json
from pathlib import Path

from src.routes.gpu_public import build_public_feed_schema

OUTPUT_PATH = Path(__file__).resolve().parents[1] / "docs" / "gpu" / "public-feed.schema.json"


def main() -> None:
    schema = build_public_feed_schema()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(schema, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
