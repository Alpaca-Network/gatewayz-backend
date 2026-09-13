"""Chutes-style WAYZ emission rewards (gatewayz-backend tokenomics).

See docs/tokenomics/EMISSION.md for the full design and worked example.
src/services/emission/scoring.py holds the pure scoring/split math (no DB,
no I/O -- heavily unit tested); src/services/emission/epoch.py orchestrates
the daily job around it.
"""
