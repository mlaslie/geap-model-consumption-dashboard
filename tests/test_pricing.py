"""
Tests for normalize_model_name, resolve_pricing, and calculate_estimated_cost
in backend.bq_client.

All tests use an in-memory pricing dict so they are independent of pricing.json
on disk.
"""
import pytest
from backend.bq_client import normalize_model_name, resolve_pricing, calculate_estimated_cost

# ---------------------------------------------------------------------------
# Pricing fixture used by arithmetic tests
# (kept at old flash values so existing arithmetic assertions need no change)
# ---------------------------------------------------------------------------
PRICING = {
    "gemini-2.5-pro": {"input_cost_per_million": 1.25, "output_cost_per_million": 5.00},
    "gemini-2.5-flash": {"input_cost_per_million": 0.075, "output_cost_per_million": 0.30},
}

# ---------------------------------------------------------------------------
# Richer fixture for tier / region tests
# ---------------------------------------------------------------------------
PRICING_FULL = {
    "gemini-2.5-pro": {
        "input_cost_per_million": 1.25,
        "input_cost_per_million_gt_200k": 2.50,
        "output_cost_per_million": 10.00,
    },
    "gemini-3.5-flash": {
        "input_cost_per_million": 1.50,
        "output_cost_per_million": 9.00,
        "non_global_multiplier": 1.1,
    },
    "gemini-2.5-flash": {
        "input_cost_per_million": 0.30,
        "output_cost_per_million": 2.50,
    },
}


# ---------------------------------------------------------------------------
# normalize_model_name
# ---------------------------------------------------------------------------

class TestNormalizeModelName:
    def test_plain_name_unchanged(self):
        assert normalize_model_name("gemini-2.5-pro") == "gemini-2.5-pro"

    def test_strips_publishers_prefix(self):
        result = normalize_model_name("publishers/google/models/gemini-2.5-pro")
        assert result == "gemini-2.5-pro"

    def test_strips_prefix_and_lowercases(self):
        # The function lowercases before stripping
        result = normalize_model_name("publishers/google/models/Gemini-2.5-Flash")
        assert result == "gemini-2.5-flash"

    def test_versioned_model_strips_prefix(self):
        # 'gemini-2.5-flash-001' after stripping the prefix
        result = normalize_model_name("publishers/google/models/gemini-2.5-flash-001")
        assert result == "gemini-2.5-flash-001"

    def test_empty_string_returns_unknown(self):
        assert normalize_model_name("") == "unknown"

    def test_none_returns_unknown(self):
        # normalize_model_name accepts None via 'if not model_name' guard
        assert normalize_model_name(None) == "unknown"  # type: ignore[arg-type]

    def test_already_lowercase(self):
        assert normalize_model_name("gemini-2.5-flash") == "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# calculate_estimated_cost — match resolution
# ---------------------------------------------------------------------------

class TestCalculateEstimatedCostResolution:
    def test_exact_match_pro(self):
        # 1 M input tokens at pro rates = 1.25 / M  → $1.25; 0 output
        cost = calculate_estimated_cost("gemini-2.5-pro", 1_000_000, 0, PRICING)
        assert cost == pytest.approx(1.25, rel=1e-6)

    def test_exact_match_flash(self):
        # 1 M output tokens at flash rates = 0.30 / M → $0.30; 0 input
        cost = calculate_estimated_cost("gemini-2.5-flash", 0, 1_000_000, PRICING)
        assert cost == pytest.approx(0.30, rel=1e-6)

    def test_longest_prefix_match_versioned_flash(self):
        """'gemini-2.5-flash-001' has no exact entry; should match 'gemini-2.5-flash'."""
        cost = calculate_estimated_cost(
            "publishers/google/models/gemini-2.5-flash-001",
            1_000_000, 0, PRICING
        )
        # flash input rate = 0.075 / M tokens
        assert cost == pytest.approx(0.075, rel=1e-6)

    def test_prefix_match_uses_longest_not_shortest(self):
        """
        When multiple prefixes match, the longest wins.
        'gemini-2.5-flash-exp' should match 'gemini-2.5-flash' (not a shorter key).
        """
        extended_pricing = {
            **PRICING,
            "gemini-2.5": {"input_cost_per_million": 99.0, "output_cost_per_million": 99.0},
        }
        cost = calculate_estimated_cost("gemini-2.5-flash-exp", 1_000_000, 0, extended_pricing)
        # Should match 'gemini-2.5-flash', not 'gemini-2.5'
        assert cost == pytest.approx(0.075, rel=1e-6)

    def test_unknown_model_uses_default_flash_rates(self):
        """Unrecognised model falls back to current flash-class defaults (1.50 / 7.50 per M)."""
        cost = calculate_estimated_cost(
            "some-totally-unknown-model-xyz", 1_000_000, 0, PRICING
        )
        assert cost == pytest.approx(1.50, rel=1e-6)

    def test_empty_model_name_uses_default_flash_rates(self):
        """Empty string normalises to 'unknown' → no match → default output rate (7.50/M)."""
        cost = calculate_estimated_cost("", 0, 1_000_000, PRICING)
        assert cost == pytest.approx(7.50, rel=1e-6)


# ---------------------------------------------------------------------------
# calculate_estimated_cost — arithmetic correctness
# ---------------------------------------------------------------------------

class TestCalculateEstimatedCostArithmetic:
    def test_zero_tokens_produces_zero_cost(self):
        cost = calculate_estimated_cost("gemini-2.5-pro", 0, 0, PRICING)
        assert cost == 0.0

    def test_combined_input_output_pro(self):
        # 2 M input @ 1.25 + 1 M output @ 5.00 = 2.50 + 5.00 = 7.50
        cost = calculate_estimated_cost("gemini-2.5-pro", 2_000_000, 1_000_000, PRICING)
        assert cost == pytest.approx(7.50, rel=1e-6)

    def test_combined_input_output_flash(self):
        # 500k input @ 0.075/M = 0.0375; 200k output @ 0.30/M = 0.06 → 0.0975
        cost = calculate_estimated_cost("gemini-2.5-flash", 500_000, 200_000, PRICING)
        expected = (500_000 / 1_000_000 * 0.075) + (200_000 / 1_000_000 * 0.30)
        assert cost == pytest.approx(expected, rel=1e-6)

    def test_result_rounded_to_six_places(self):
        # Result should be rounded to 6 decimal places per the implementation
        cost = calculate_estimated_cost("gemini-2.5-flash", 1, 1, PRICING)
        assert cost == round(cost, 6)

    def test_prefix_match_arithmetic_flash_001(self):
        """1M in + 1M out via prefix-matched flash rates."""
        cost = calculate_estimated_cost(
            "publishers/google/models/gemini-2.5-flash-001",
            1_000_000, 1_000_000, PRICING
        )
        expected = 0.075 + 0.30  # = 0.375
        assert cost == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# resolve_pricing — match kinds
# ---------------------------------------------------------------------------

class TestResolvePricing:
    """Tests for the resolve_pricing helper (returns (rates, match_kind))."""

    def test_exact_match_returns_kind_exact(self):
        rates, kind = resolve_pricing("gemini-2.5-pro", PRICING)
        assert kind == "exact"
        assert rates == PRICING["gemini-2.5-pro"]

    def test_exact_match_flash_returns_kind_exact(self):
        rates, kind = resolve_pricing("gemini-2.5-flash", PRICING)
        assert kind == "exact"
        assert rates is not None
        assert "input_cost_per_million" in rates

    def test_prefix_match_versioned_returns_kind_prefix(self):
        """'gemini-2.5-flash-001' has no exact entry; should prefix-match 'gemini-2.5-flash'."""
        rates, kind = resolve_pricing("gemini-2.5-flash-001", PRICING)
        assert kind == "prefix"
        assert rates == PRICING["gemini-2.5-flash"]

    def test_no_match_returns_none_and_kind_default(self):
        rates, kind = resolve_pricing("totally-unknown-model-xyz", PRICING)
        assert kind == "default"
        assert rates is None

    def test_publishers_prefix_exact_match(self):
        """publishers/google/models/gemini-2.5-pro normalizes to exact match."""
        rates, kind = resolve_pricing("publishers/google/models/gemini-2.5-pro", PRICING)
        assert kind == "exact"

    def test_publishers_prefix_versioned_is_prefix(self):
        """publishers/google/models/gemini-2.5-flash-exp normalizes → prefix match."""
        rates, kind = resolve_pricing("publishers/google/models/gemini-2.5-flash-exp", PRICING)
        assert kind == "prefix"

    def test_empty_model_name_returns_default(self):
        """Empty string normalizes to 'unknown' → no match → default."""
        rates, kind = resolve_pricing("", PRICING)
        assert kind == "default"
        assert rates is None

    def test_prefix_longest_wins(self):
        """When multiple prefixes match, the longest key wins."""
        extended = {
            **PRICING,
            "gemini-2.5": {"input_cost_per_million": 99.0, "output_cost_per_million": 99.0},
        }
        rates, kind = resolve_pricing("gemini-2.5-flash-exp", extended)
        assert kind == "prefix"
        # Must have matched the longer 'gemini-2.5-flash', not the shorter 'gemini-2.5'
        assert rates["input_cost_per_million"] == pytest.approx(0.075)


# ---------------------------------------------------------------------------
# Context-tier pricing (gt200k / le200k)
# ---------------------------------------------------------------------------

class TestContextTierPricing:
    """calculate_estimated_cost applies the gt200k input rate when the tier
    field is present in the pricing entry AND tier=='gt200k' is requested."""

    def test_le200k_uses_standard_input_rate(self):
        """Default tier 'le200k' always uses input_cost_per_million."""
        cost = calculate_estimated_cost(
            "gemini-2.5-pro", 1_000_000, 0, PRICING_FULL, tier="le200k"
        )
        # 1M input @ $1.25/M
        assert cost == pytest.approx(1.25, rel=1e-6)

    def test_gt200k_uses_gt200k_rate_when_field_present(self):
        """When tier='gt200k' and input_cost_per_million_gt_200k is in the entry,
        use the gt200k rate."""
        cost = calculate_estimated_cost(
            "gemini-2.5-pro", 1_000_000, 0, PRICING_FULL, tier="gt200k"
        )
        # 1M input @ $2.50/M (gt200k rate)
        assert cost == pytest.approx(2.50, rel=1e-6)

    def test_gt200k_falls_back_to_standard_when_field_absent(self):
        """When tier='gt200k' but the entry has no gt200k field, use standard rate."""
        cost = calculate_estimated_cost(
            "gemini-2.5-flash", 1_000_000, 0, PRICING_FULL, tier="gt200k"
        )
        # gemini-2.5-flash has no gt200k field → $0.30/M
        assert cost == pytest.approx(0.30, rel=1e-6)

    def test_gt200k_output_rate_unaffected(self):
        """Output rate is never tiered; gt200k only changes the input rate."""
        cost_le = calculate_estimated_cost(
            "gemini-2.5-pro", 0, 1_000_000, PRICING_FULL, tier="le200k"
        )
        cost_gt = calculate_estimated_cost(
            "gemini-2.5-pro", 0, 1_000_000, PRICING_FULL, tier="gt200k"
        )
        # Both should use output_cost_per_million = 10.00
        assert cost_le == pytest.approx(10.00, rel=1e-6)
        assert cost_gt == pytest.approx(10.00, rel=1e-6)

    def test_combined_gt200k_in_plus_output(self):
        """End-to-end: 1M gt200k input @ $2.50 + 1M output @ $10.00 = $12.50."""
        cost = calculate_estimated_cost(
            "gemini-2.5-pro", 1_000_000, 1_000_000, PRICING_FULL, tier="gt200k"
        )
        assert cost == pytest.approx(12.50, rel=1e-6)


# ---------------------------------------------------------------------------
# Regional endpoint pricing (non_global_multiplier)
# ---------------------------------------------------------------------------

class TestRegionMultiplierPricing:
    """When region != 'global', both input and output rates are multiplied by
    non_global_multiplier (default 1.0 when the field is absent)."""

    def test_global_region_no_multiplier(self):
        """Default region='global' — no multiplier applied."""
        cost = calculate_estimated_cost(
            "gemini-3.5-flash", 1_000_000, 1_000_000, PRICING_FULL, region="global"
        )
        # 1M in @ $1.50 + 1M out @ $9.00 = $10.50
        assert cost == pytest.approx(10.50, rel=1e-6)

    def test_regional_endpoint_multiplies_both_rates(self):
        """region='regional' multiplies both input and output by non_global_multiplier=1.1."""
        cost = calculate_estimated_cost(
            "gemini-3.5-flash", 1_000_000, 1_000_000, PRICING_FULL, region="regional"
        )
        # (1.50 * 1.1) + (9.00 * 1.1) = 1.65 + 9.90 = 11.55
        assert cost == pytest.approx(11.55, rel=1e-6)

    def test_regional_endpoint_no_multiplier_field_means_1x(self):
        """When non_global_multiplier is absent, regional endpoint still uses 1.0."""
        cost_global = calculate_estimated_cost(
            "gemini-2.5-flash", 1_000_000, 1_000_000, PRICING_FULL, region="global"
        )
        cost_regional = calculate_estimated_cost(
            "gemini-2.5-flash", 1_000_000, 1_000_000, PRICING_FULL, region="regional"
        )
        # gemini-2.5-flash has no non_global_multiplier → no change
        assert cost_global == pytest.approx(cost_regional, rel=1e-6)

    def test_regional_with_gt200k_tier(self):
        """Tier and region can both be non-default simultaneously."""
        cost = calculate_estimated_cost(
            "gemini-3.5-flash", 1_000_000, 0, PRICING_FULL,
            tier="gt200k", region="regional"
        )
        # gemini-3.5-flash has no gt200k field → falls back to $1.50, then * 1.1 = $1.65
        assert cost == pytest.approx(1.65, rel=1e-6)


# ---------------------------------------------------------------------------
# Backward compatibility — optional fields absent from pricing dict
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """Pricing dicts without optional fields continue to work as before."""

    def test_dict_without_optional_fields_works(self):
        """A pricing entry with only the two required fields computes correctly."""
        simple = {
            "my-model": {"input_cost_per_million": 2.0, "output_cost_per_million": 8.0}
        }
        cost = calculate_estimated_cost("my-model", 1_000_000, 1_000_000, simple)
        assert cost == pytest.approx(10.0, rel=1e-6)

    def test_dict_without_gt200k_ignores_tier_arg(self):
        """Requesting gt200k tier for a model without the field silently uses standard rate."""
        simple = {"my-model": {"input_cost_per_million": 2.0, "output_cost_per_million": 8.0}}
        cost_le = calculate_estimated_cost("my-model", 1_000_000, 0, simple, tier="le200k")
        cost_gt = calculate_estimated_cost("my-model", 1_000_000, 0, simple, tier="gt200k")
        assert cost_le == pytest.approx(2.0, rel=1e-6)
        assert cost_gt == pytest.approx(2.0, rel=1e-6)  # no gt200k field → same rate

    def test_dict_without_multiplier_ignores_region_arg(self):
        """Requesting regional endpoint for a model without multiplier uses 1.0x."""
        simple = {"my-model": {"input_cost_per_million": 2.0, "output_cost_per_million": 8.0}}
        cost_global = calculate_estimated_cost("my-model", 1_000_000, 0, simple, region="global")
        cost_regional = calculate_estimated_cost("my-model", 1_000_000, 0, simple, region="us-central1")
        assert cost_global == pytest.approx(2.0, rel=1e-6)
        assert cost_regional == pytest.approx(2.0, rel=1e-6)
