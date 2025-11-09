# Multi-Provider Registry

The gateway now keeps a **canonical registry** of every logical model and all of the providers
that can serve it. This registry powers catalog responses, automatic failover, and
provider-specific routing metadata (costs, features, availability).

## How the registry works

1. **Provider fetchers** push their normalized catalogs into the registry via
   `sync_provider_catalog(provider_name, models)`. This happens automatically from
   `src/services/models.py` and `src/services/portkey_providers.py`.
2. Each model entry is keyed by `canonical_slug` (or `slug`/`id` as a fallback) and
   tracks every provider's pricing, capabilities, and provider-specific model ID.
3. The registry exposes a canonical snapshot that backs `get_cached_models("all")`.
   Downstream consumers (pricing, catalog endpoints, etc.) should treat the registry
   as the source of truth instead of manually merging provider caches.
4. `ProviderSelector` consumes the registry and builds an ordered plan of providers
   for every request, applying circuit breaking and provider priorities.

## Registering a new provider / model

1. Ensure the provider's fetcher returns normalized models with:
   - `id`: provider-specific ID (what the upstream API expects)
   - `canonical_slug`: shared logical identifier (e.g., `google/gemini-2.0-flash`)
   - `pricing`, `context_length`, `source_gateway`, etc.
2. After the fetcher caches results, call the helper in `src/services/models.py`:

   ```python
   return _sync_registry("provider-name", normalized_models)
   ```

   For providers defined in `src/services/portkey_providers.py`, use `_cache_normalized_models`
   which already syncs the registry.
3. If a model is available across multiple providers, ensure each provider reports the
   same `canonical_slug`. The registry will merge them automatically and expose a single
   logical model with multiple `ProviderConfig` entries.
4. Re-run the failure-plan tests to confirm the model fans out across providers:

   ```bash
   pytest tests/services/test_multi_provider_registry.py
   pytest tests/services/test_provider_failover.py -k registry_plan
   ```

## Provider failover & routing

- `src/routes/chat.py` and `src/routes/messages.py` call
  `plan_provider_attempts(model_id, provider)` which delegates to `ProviderSelector`.
- The selector returns a list of `ProviderAttempt` objects that include both the provider
  name **and** the provider-specific model ID pulled from the registry.
- Every success/failure is recorded via `provider_selector.record_success/failure`, enabling
  circuit breaking without additional code in the routes.
- `build_provider_failover_chain(initial_provider, model_id=...)` now defers to this plan,
  so legacy callers still gain registry-driven ordering when they pass a model ID.

## Useful references

- Canonical ingestion helper: `src/services/models._sync_registry`
- Provider selector & circuit breaker: `src/services/provider_selector.py`
- Registry data model: `src/services/multi_provider_registry.py`
- Tests validating end-to-end behavior:
  - `tests/services/test_multi_provider_registry.py`
  - `tests/services/test_provider_failover.py` (registry plan test)
