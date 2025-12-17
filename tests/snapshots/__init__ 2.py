"""
Snapshot Tests

Snapshot testing for API responses to detect unintended changes.

Snapshots capture the structure and content of API responses.
If a response changes, the test will fail and show the diff.

Run with:
    pytest tests/snapshots/ -v

Update snapshots after intentional changes:
    pytest tests/snapshots/ --snapshot-update
"""
