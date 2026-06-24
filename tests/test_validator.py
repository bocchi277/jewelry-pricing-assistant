"""Unit tests for validator.py -- coercion and warning detection."""
import math

import validator


def test_to_float_blank_defaults_to_zero_no_warning():
    value, warning = validator.to_float(None, "gold_weight_grams")
    assert value == 0.0
    assert warning is None

    value, warning = validator.to_float("", "gold_weight_grams")
    assert value == 0.0
    assert warning is None

    value, warning = validator.to_float(math.nan, "gold_weight_grams")
    assert value == 0.0
    assert warning is None


def test_to_float_non_numeric_warns_and_defaults():
    value, warning = validator.to_float("abc", "labor_cost")
    assert value == 0.0
    assert warning == ("NON_NUMERIC_VALUE", {"field": "labor_cost", "raw": "abc"})


def test_to_float_negative_clamped_with_warning():
    value, warning = validator.to_float(-5, "gold_weight_grams")
    assert value == 0.0
    assert warning[0] == "NEGATIVE_VALUE_CLAMPED"


def test_to_float_valid_number_passes_through_clean():
    value, warning = validator.to_float("4.8", "gold_weight_grams")
    assert value == 4.8
    assert warning is None


def test_to_str_blank_returns_none():
    assert validator.to_str(None) is None
    assert validator.to_str("") is None
    assert validator.to_str("   ") is None
    assert validator.to_str(math.nan) is None


def test_to_str_strips_whitespace():
    assert validator.to_str("  Ruby  ") == "Ruby"


def test_cross_field_missing_diamond_quality():
    warnings = validator.cross_field_warnings(1.2, None, False, None, 0.0, False, "14K", 10.0, False)
    assert ("MISSING_DIAMOND_QUALITY", {}) in warnings


def test_cross_field_no_warning_when_quality_present():
    warnings = validator.cross_field_warnings(1.2, "VS", False, None, 0.0, False, "14K", 10.0, False)
    assert warnings == []


def test_cross_field_color_stone_carat_without_type():
    warnings = validator.cross_field_warnings(0.0, None, False, None, 1.1, False, "14K", 10.0, False)
    codes = [w[0] for w in warnings]
    assert "COLOR_STONE_CARAT_WITHOUT_TYPE" in codes


def test_cross_field_missing_cpc():
    warnings = validator.cross_field_warnings(1.2, "VS", True, "Ruby", 1.0, True, "14K", 10.0, False)
    codes = [w[0] for w in warnings]
    assert "MISSING_DIAMOND_COST_PER_CARAT" in codes
    assert "MISSING_COLOR_STONE_COST_PER_CARAT" in codes


def test_cross_field_missing_gold_weight():
    warnings = validator.cross_field_warnings(0.0, None, False, None, 0.0, False, "14K", 0.0, True)
    codes = [w[0] for w in warnings]
    assert "MISSING_GOLD_WEIGHT" in codes


def test_cross_field_color_stone_type_without_carat():
    warnings = validator.cross_field_warnings(0.0, None, False, "Ruby", 0.0, False, "14K", 10.0, False)
    codes = [w[0] for w in warnings]
    assert "COLOR_STONE_TYPE_WITHOUT_CARAT" in codes


def test_render_warning_formats_template():
    text = validator.render_warning("UNKNOWN_METAL_CODE", {"code": "ZZ"})
    assert "ZZ" in text
    assert "metal_prices.csv" in text


def test_render_warning_unknown_code_falls_back_to_code_itself():
    assert validator.render_warning("SOME_NEW_CODE", {}) == "SOME_NEW_CODE"


def test_render_warnings_preserves_order_and_count():
    codes = [("MISSING_DIAMOND_QUALITY", {}), ("UNKNOWN_METAL_CODE", {"code": "XX"})]
    rendered = validator.render_warnings(codes)
    assert len(rendered) == 2
    assert "quality" in rendered[0]
    assert "XX" in rendered[1]


# ---- Task 4: new informational warnings ----

def test_cross_field_zero_diamond_cost_per_carat():
    """Task 4: diamond_carat > 0 with cost_per_carat == 0 (not blank)
    should produce ZERO_COST_PER_CARAT warning."""
    warnings = validator.cross_field_warnings(
        1.2, "VS", False, None, 0.0, False, "14W", 5.0, False,
        diamond_cost_per_carat=0.0,
    )
    codes = [w[0] for w in warnings]
    assert "ZERO_COST_PER_CARAT" in codes


def test_cross_field_zero_color_stone_cost_per_carat():
    """Task 4: color_stone_carat > 0 with cost_per_carat == 0 (not blank)
    should produce ZERO_COST_PER_CARAT warning."""
    warnings = validator.cross_field_warnings(
        0.0, None, False, "Ruby", 1.5, False, "14W", 5.0, False,
        color_stone_cost_per_carat=0.0,
    )
    codes = [w[0] for w in warnings]
    assert "ZERO_COST_PER_CARAT" in codes


def test_cross_field_zero_gold_weight_with_valid_metal():
    """Task 4: gold_weight == 0 (not blank) with a resolved metal code
    should produce ZERO_GOLD_WEIGHT."""
    warnings = validator.cross_field_warnings(
        0.0, None, False, None, 0.0, False, "14W", 0.0, False,
        metal_resolved=True,
    )
    codes = [w[0] for w in warnings]
    assert "ZERO_GOLD_WEIGHT" in codes


def test_cross_field_no_zero_gold_weight_when_blank():
    """Task 4: blank gold_weight should produce MISSING_GOLD_WEIGHT,
    NOT ZERO_GOLD_WEIGHT."""
    warnings = validator.cross_field_warnings(
        0.0, None, False, None, 0.0, False, "14W", 0.0, True,
        metal_resolved=True,
    )
    codes = [w[0] for w in warnings]
    assert "MISSING_GOLD_WEIGHT" in codes
    assert "ZERO_GOLD_WEIGHT" not in codes


def test_cross_field_no_zero_cpc_when_blank():
    """Task 4: blank cost-per-carat should produce MISSING_..._COST_PER_CARAT,
    NOT ZERO_COST_PER_CARAT."""
    warnings = validator.cross_field_warnings(
        1.2, "VS", True, None, 0.0, False, "14W", 5.0, False,
        diamond_cost_per_carat=0.0,  # value is 0 because it defaulted from blank
    )
    codes = [w[0] for w in warnings]
    assert "MISSING_DIAMOND_COST_PER_CARAT" in codes
    # diamond_cpc_blank=True means the check won't fire ZERO_COST_PER_CARAT
    assert "ZERO_COST_PER_CARAT" not in codes


# ---- Task 1: NON_FINITE_RESULT template exists ----

def test_non_finite_result_warning_template_exists():
    """Task 1: the NON_FINITE_RESULT template should exist and render."""
    text = validator.render_warning("NON_FINITE_RESULT", {"field": "metal_cost"})
    assert "metal_cost" in text
    assert "non-finite" in text.lower()
