#!/usr/bin/env python3
"""
Helper script to add circuit breaker integration to provider clients.

This script generates the boilerplate code for integrating circuit breakers
into provider client files.

Usage:
    python scripts/add_circuit_breakers.py anthropic deepinfra cerebras
"""

import sys

CIRCUIT_BREAKER_IMPORTS = """from src.services.circuit_breaker import CircuitBreakerConfig, CircuitBreakerError, get_circuit_breaker
from src.utils.sentry_context import capture_provider_error"""

def generate_config(provider_name: str) -> str:
    """Generate circuit breaker configuration for a provider."""
    return f"""
# Circuit breaker configuration for {provider_name.title()}
{provider_name.upper()}_CIRCUIT_CONFIG = CircuitBreakerConfig(
    failure_threshold=5,
    success_threshold=2,
    timeout_seconds=60,
    failure_window_seconds=60,
    failure_rate_threshold=0.5,
    min_requests_for_rate=10,
)
"""

def generate_wrapper_functions(provider_name: str, original_func_name: str, is_streaming: bool = False) -> str:
    """Generate internal and wrapper functions for circuit breaker integration."""

    stream_suffix = "_stream" if is_streaming else ""
    stream_note = " (streaming)" if is_streaming else ""
    endpoint_suffix = " (stream)" if is_streaming else ""

    return f"""
def _{original_func_name}_internal(messages, model, **kwargs):
    \"\"\"Internal function to make{" streaming" if is_streaming else ""} request to {provider_name.title()} (called by circuit breaker).\"\"\"
    client = get_{provider_name}_client()
    {"stream = client.chat.completions.create(model=model, messages=messages, stream=True, **kwargs)" if is_streaming else "response = client.chat.completions.create(model=model, messages=messages, **kwargs)"}
    return {"stream" if is_streaming else "response"}


def {original_func_name}(messages, model, **kwargs):
    \"\"\"Make{" streaming" if is_streaming else ""} request to {provider_name.title()} with circuit breaker protection.\"\"\"
    circuit_breaker = get_circuit_breaker("{provider_name}", {provider_name.upper()}_CIRCUIT_CONFIG)

    try:
        {"stream" if is_streaming else "response"} = circuit_breaker.call(
            _{original_func_name}_internal,
            messages,
            model,
            **kwargs
        )
        return {"stream" if is_streaming else "response"}
    except CircuitBreakerError as e:
        logger.warning(f"{provider_name.title()} circuit breaker OPEN{stream_note}: {{e.message}}")
        capture_provider_error(
            e,
            provider='{provider_name}',
            model=model,
            endpoint='/chat/completions{endpoint_suffix}',
            extra_context={{"circuit_breaker_state": e.state.value}}
        )
        raise
    except Exception as e:
        logger.error(f"{provider_name.title()}{" streaming" if is_streaming else ""} request failed: {{e}}")
        capture_provider_error(e, provider='{provider_name}', model=model, endpoint='/chat/completions{endpoint_suffix}')
        raise
"""

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/add_circuit_breakers.py <provider1> [provider2] ...")
        print("Example: python scripts/add_circuit_breakers.py anthropic deepinfra cerebras")
        sys.exit(1)

    providers = sys.argv[1:]

    print("Circuit Breaker Integration Code Generator")
    print("=" * 60)
    print()

    for provider in providers:
        print(f"\n{'='*60}")
        print(f"Provider: {provider.upper()}")
        print(f"{'='*60}\n")

        print("1. Add these imports at the top:")
        print(CIRCUIT_BREAKER_IMPORTS)

        print("\n2. Add circuit breaker config after imports:")
        print(generate_config(provider))

        print("\n3. Replace normal request function with:")
        print(generate_wrapper_functions(provider, f"make_{provider}_request_openai", is_streaming=False))

        print("\n4. Replace streaming request function with:")
        print(generate_wrapper_functions(provider, f"make_{provider}_request_openai_stream", is_streaming=True))

        print("\n" + "="*60)

if __name__ == "__main__":
    main()
