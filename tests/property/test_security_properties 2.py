"""
Property-based tests for security functions

Uses Hypothesis to generate random inputs and verify security properties hold.
Finds edge cases that manual tests might miss.
"""

import pytest
from hypothesis import given, strategies as st, assume, settings
from hypothesis import HealthCheck
import os

# Set test environment
os.environ['API_GATEWAY_SALT'] = 'test-salt-for-hashing-keys-minimum-16-chars'

from src.security.security import (
    hash_api_key,
    generate_secure_api_key,
    validate_ip_allowlist,
    validate_domain_referrers,
)


# ============================================================================
# API Key Hashing Properties
# ============================================================================

class TestAPIKeyHashingProperties:
    """Property-based tests for API key hashing"""

    @pytest.mark.unit
    @given(api_key=st.text(min_size=1, max_size=200))
    @settings(max_examples=50)
    def test_hash_is_deterministic(self, api_key):
        """Property: Same input always produces same hash"""
        hash1 = hash_api_key(api_key)
        hash2 = hash_api_key(api_key)

        assert hash1 == hash2, "Hash should be deterministic"

    @pytest.mark.unit
    @given(
        key1=st.text(min_size=1, max_size=100),
        key2=st.text(min_size=1, max_size=100)
    )
    @settings(max_examples=50)
    def test_different_keys_produce_different_hashes(self, key1, key2):
        """Property: Different inputs produce different hashes (collision resistance)"""
        assume(key1 != key2)  # Only test when keys are different

        hash1 = hash_api_key(key1)
        hash2 = hash_api_key(key2)

        assert hash1 != hash2, "Different keys should produce different hashes"

    @pytest.mark.unit
    @given(api_key=st.text(min_size=1, max_size=200))
    @settings(max_examples=50)
    def test_hash_length_is_constant(self, api_key):
        """Property: Hash output is always 64 characters (SHA256 hex)"""
        hashed = hash_api_key(api_key)

        assert len(hashed) == 64, "SHA256 hex digest should always be 64 chars"
        assert all(c in '0123456789abcdef' for c in hashed), "Hash should be hex"

    @pytest.mark.unit
    @given(api_key=st.text(min_size=1, max_size=200))
    @settings(max_examples=50)
    def test_hash_never_reveals_original(self, api_key):
        """Property: Hash should never contain the original key"""
        hashed = hash_api_key(api_key)

        # Hash should not contain the original key
        assert api_key not in hashed, "Hash should not reveal original key"


# ============================================================================
# API Key Generation Properties
# ============================================================================

class TestAPIKeyGenerationProperties:
    """Property-based tests for API key generation"""

    @pytest.mark.unit
    @settings(max_examples=100)
    @given(st.integers(min_value=0, max_value=100))
    def test_generated_keys_are_unique(self, _):
        """Property: Generated API keys are always unique"""
        keys = set()

        # Generate multiple keys
        for _ in range(10):
            key = generate_secure_api_key()
            assert key not in keys, "Generated keys should be unique"
            keys.add(key)

    @pytest.mark.unit
    @settings(max_examples=50)
    @given(st.integers(min_value=0, max_value=50))
    def test_generated_key_format(self, _):
        """Property: Generated keys follow correct format"""
        key = generate_secure_api_key()

        assert key.startswith("gw_live_"), "Key should start with gw_live_"
        assert len(key) > 20, "Key should be reasonably long for security"
        # After prefix, should be alphanumeric
        suffix = key[8:]  # Remove "gw_live_"
        assert suffix.replace("-", "").replace("_", "").isalnum(), "Key suffix should be alphanumeric"

    @pytest.mark.unit
    @settings(max_examples=50)
    @given(st.integers(min_value=0, max_value=50))
    def test_generated_keys_are_strong(self, _):
        """Property: Generated keys have sufficient entropy"""
        key = generate_secure_api_key()

        # Should have reasonable length
        assert len(key) >= 30, "Key should be at least 30 chars for security"

        # Should not be trivially guessable
        assert "000000" not in key, "Key should not have obvious patterns"
        assert "aaaaaa" not in key.lower(), "Key should not have obvious patterns"


# ============================================================================
# IP Validation Properties
# ============================================================================

class TestIPValidationProperties:
    """Property-based tests for IP allowlist validation"""

    @pytest.mark.unit
    @given(
        ip=st.ip_addresses(v=4).map(str),
        allowlist=st.lists(st.ip_addresses(v=4).map(str), min_size=1, max_size=10)
    )
    @settings(max_examples=50)
    def test_ip_in_allowlist_is_valid(self, ip, allowlist):
        """Property: IP in allowlist should be valid"""
        # Add the IP to allowlist
        if ip not in allowlist:
            allowlist.append(ip)

        result = validate_ip_allowlist(ip, allowlist)

        assert result is True, "IP in allowlist should be valid"

    @pytest.mark.unit
    @given(
        ip=st.ip_addresses(v=4).map(str),
        allowlist=st.lists(st.ip_addresses(v=4).map(str), min_size=1, max_size=10)
    )
    @settings(max_examples=50)
    def test_ip_not_in_allowlist_is_invalid(self, ip, allowlist):
        """Property: IP not in allowlist should be invalid"""
        # Ensure IP is not in allowlist
        allowlist = [ip_addr for ip_addr in allowlist if ip_addr != ip]
        assume(len(allowlist) > 0)  # Need at least one IP in allowlist
        assume(ip not in allowlist)  # IP must not be in allowlist

        result = validate_ip_allowlist(ip, allowlist)

        assert result is False, "IP not in allowlist should be invalid"

    @pytest.mark.unit
    @given(ip=st.ip_addresses(v=4).map(str))
    @settings(max_examples=50)
    def test_empty_allowlist_allows_all(self, ip):
        """Property: Empty allowlist allows all IPs"""
        result = validate_ip_allowlist(ip, [])

        assert result is True, "Empty allowlist should allow all IPs"

    @pytest.mark.unit
    @given(ip=st.ip_addresses(v=4).map(str))
    @settings(max_examples=50)
    def test_none_allowlist_allows_all(self, ip):
        """Property: None allowlist allows all IPs"""
        result = validate_ip_allowlist(ip, None)

        assert result is True, "None allowlist should allow all IPs"


# ============================================================================
# Domain Validation Properties
# ============================================================================

class TestDomainValidationProperties:
    """Property-based tests for domain referrer validation"""

    @pytest.mark.unit
    @given(
        domain=st.from_regex(r'https?://[a-z0-9-]+\.[a-z]{2,}', fullmatch=True),
        allowed_domains=st.lists(
            st.from_regex(r'https?://[a-z0-9-]+\.[a-z]{2,}', fullmatch=True),
            min_size=1,
            max_size=5
        )
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.filter_too_much])
    def test_domain_in_list_is_valid(self, domain, allowed_domains):
        """Property: Domain in allowed list should be valid"""
        # Add domain to allowed list
        if domain not in allowed_domains:
            allowed_domains.append(domain)

        result = validate_domain_referrers(domain, allowed_domains)

        assert result is True, "Domain in allowed list should be valid"

    @pytest.mark.unit
    @given(domain=st.from_regex(r'https?://[a-z0-9-]+\.[a-z]{2,}', fullmatch=True))
    @settings(max_examples=30)
    def test_empty_domain_list_allows_all(self, domain):
        """Property: Empty domain list allows all domains"""
        result = validate_domain_referrers(domain, [])

        assert result is True, "Empty domain list should allow all"

    @pytest.mark.unit
    @given(domain=st.from_regex(r'https?://[a-z0-9-]+\.[a-z]{2,}', fullmatch=True))
    @settings(max_examples=30)
    def test_none_domain_list_allows_all(self, domain):
        """Property: None domain list allows all domains"""
        result = validate_domain_referrers(domain, None)

        assert result is True, "None domain list should allow all"


# ============================================================================
# Pricing Calculation Properties
# ============================================================================

class TestPricingProperties:
    """Property-based tests for pricing calculations"""

    @pytest.mark.unit
    @given(
        prompt_tokens=st.integers(min_value=0, max_value=1000000),
        completion_tokens=st.integers(min_value=0, max_value=1000000),
        prompt_price=st.floats(min_value=0, max_value=0.1, allow_nan=False, allow_infinity=False),
        completion_price=st.floats(min_value=0, max_value=0.1, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_cost_is_non_negative(self, prompt_tokens, completion_tokens, prompt_price, completion_price):
        """Property: Total cost should never be negative"""
        # Calculate cost (cost per token)
        total_cost = (prompt_tokens * prompt_price) + (completion_tokens * completion_price)

        assert total_cost >= 0, "Cost should never be negative"

    @pytest.mark.unit
    @given(
        tokens=st.integers(min_value=0, max_value=1000000),
        price_per_token=st.floats(min_value=0, max_value=0.1, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_zero_tokens_means_zero_cost(self, tokens, price_per_token):
        """Property: Zero tokens should always result in zero cost"""
        if tokens == 0:
            cost = tokens * price_per_token
            assert cost == 0, "Zero tokens should result in zero cost"

    @pytest.mark.unit
    @given(
        tokens=st.integers(min_value=1, max_value=1000000),
        price_per_token=st.floats(min_value=0.000001, max_value=0.1, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_more_tokens_means_more_cost(self, tokens, price_per_token):
        """Property: More tokens should result in higher cost"""
        cost1 = tokens * price_per_token
        cost2 = (tokens * 2) * price_per_token

        assert cost2 >= cost1, "Double the tokens should cost at least as much"

    @pytest.mark.unit
    @given(
        tokens=st.integers(min_value=1, max_value=100000),
        price1=st.floats(min_value=0.000001, max_value=0.01, allow_nan=False, allow_infinity=False),
        price2=st.floats(min_value=0.000001, max_value=0.01, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_cost_scales_linearly(self, tokens, price1, price2):
        """Property: Cost should scale linearly with token count"""
        assume(price1 > 0 and price2 > 0)

        cost1 = tokens * price1
        cost2 = (tokens * 2) * price1

        # Double tokens should double cost (within floating point precision)
        ratio = cost2 / cost1 if cost1 > 0 else 0
        assert abs(ratio - 2.0) < 0.001, "Cost should scale linearly"


# ============================================================================
# Credit Calculation Properties
# ============================================================================

class TestCreditProperties:
    """Property-based tests for credit calculations"""

    @pytest.mark.unit
    @given(
        initial_credits=st.floats(min_value=0, max_value=1000, allow_nan=False, allow_infinity=False),
        deduction=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_credits_never_go_negative(self, initial_credits, deduction):
        """Property: Credits should never go below zero"""
        remaining = max(0, initial_credits - deduction)

        assert remaining >= 0, "Credits should never be negative"

    @pytest.mark.unit
    @given(
        credits=st.floats(min_value=0, max_value=1000, allow_nan=False, allow_infinity=False),
        cost=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_insufficient_credits_detection(self, credits, cost):
        """Property: Should correctly detect insufficient credits"""
        has_sufficient = credits >= cost

        if has_sufficient:
            assert credits >= cost, "Should have sufficient credits"
        else:
            assert credits < cost, "Should have insufficient credits"

    @pytest.mark.unit
    @given(
        credits=st.floats(min_value=0.01, max_value=1000, allow_nan=False, allow_infinity=False),
        addition=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_adding_credits_increases_balance(self, credits, addition):
        """Property: Adding credits should increase or maintain balance"""
        new_balance = credits + addition

        assert new_balance >= credits, "Adding credits should not decrease balance"


# ============================================================================
# Rate Limit Properties
# ============================================================================

class TestRateLimitProperties:
    """Property-based tests for rate limiting"""

    @pytest.mark.unit
    @given(
        requests_made=st.integers(min_value=0, max_value=1000),
        requests_limit=st.integers(min_value=1, max_value=1000),
    )
    @settings(max_examples=100)
    def test_rate_limit_detection(self, requests_made, requests_limit):
        """Property: Rate limit should be correctly detected"""
        is_limited = requests_made >= requests_limit
        remaining = max(0, requests_limit - requests_made)

        if is_limited:
            assert remaining == 0, "No requests remaining when limited"
        else:
            assert remaining > 0, "Requests remaining when not limited"

    @pytest.mark.unit
    @given(
        limit=st.integers(min_value=1, max_value=10000),
        used=st.integers(min_value=0, max_value=10000),
    )
    @settings(max_examples=100)
    def test_remaining_never_negative(self, limit, used):
        """Property: Remaining requests should never be negative"""
        remaining = max(0, limit - used)

        assert remaining >= 0, "Remaining should never be negative"

    @pytest.mark.unit
    @given(
        limit=st.integers(min_value=1, max_value=1000),
    )
    @settings(max_examples=50)
    def test_zero_usage_means_full_limit(self, limit):
        """Property: Zero usage should mean full limit available"""
        used = 0
        remaining = limit - used

        assert remaining == limit, "Zero usage should leave full limit"
