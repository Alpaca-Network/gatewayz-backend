# Agent Instructions

For high-level project context and detailed guidelines, see [CLAUDE.MD](../CLAUDE.md).

## Quick Reference: Adding a New Gateway

To add a new gateway provider:

1. **Add to `GATEWAY_REGISTRY`** in `src/routes/catalog.py`:
```python
"new-gateway": {
    "name": "New Gateway",
    "color": "bg-purple-500",
    "priority": "slow",
    "site_url": "https://newgateway.com",
},
```

2. **Ensure models include `source_gateway: "new-gateway"`** in the model data

3. **The frontend will automatically discover and display the new gateway!**
