"""Unit tests for calculator.py -- the deterministic core.

These never touch pandas, files, or the network: pure function in,
value out. They lock in the formulas and the business rules from the
assignment spec, including the exact worked example given in the
instructions doc.
"""

import pytest

import calculator

METAL_LOOKUP = {
    "14W": ("14K", 42.0), "14Y": ("14K", 42.0), "14R": ("14K", 42.0),
    "18W": ("18K", 57.0), "18Y": ("18K", 57.0), "18R": ("18K", 57.0),
    "PT": ("PT", 38.0),
}


def test_metal_lookup_groups_share_one_price():
    for code in ("14W", "14Y", "14R"):
        group, price = METAL_LOOKUP[code]
        assert group == "14K"
        assert price == 42.0


def test_build_metal_lookup_splits_comma_separated_codes():
    rows = [
        {"metal_group": "14K", "metal_codes": "14W,14Y,14R", "price_per_gram": 42},
        {"metal_group": "PT", "metal_codes": "PT", "price_per_gram": 38},
    ]
    lookup = calculator.build_metal_lookup(rows)
    assert lookup["14W"] == ("14K", 42.0)
    assert lookup["14R"] == ("14K", 42.0)
    assert lookup["PT"] == ("PT", 38.0)


def test_resolve_metal_unknown_code_does_not_crash():
    group, price, warnings = calculator.resolve_metal("ZZ", METAL_LOOKUP)
    assert group is None
    assert price == 0.0
    assert warnings == [("UNKNOWN_METAL_CODE", {"code": "ZZ"})]


def test_resolve_metal_blank_code():
    group, price, warnings = calculator.resolve_metal("", METAL_LOOKUP)
    assert group is None
    assert price == 0.0
    assert warnings == [("MISSING_METAL_CODE", {})]


def test_spec_worked_example_matches_exactly():
    """B401400-14WVS from the assignment's own example output:
    metal_cost 201.6, diamond_cost 570.0, total 906.6,
    wholesale 2901.12, retail 5802.24.
    """
    costs = calculator.calculate_costs(
        gold_weight_grams=4.8, price_per_gram=42.0,
        diamond_carat=1.2, diamond_cost_per_carat=475.0,
        color_stone_type=None, color_stone_carat=0.0, color_stone_cost_per_carat=0.0,
        labor_cost=95.0, setting_cost=40.0, markup_percent=220.0,
    )
    assert costs["metal_cost"] == 201.6
    assert costs["diamond_cost"] == 570.0
    assert costs["color_stone_cost"] == 0.0
    assert round(costs["total_cost"], 2) == 906.6
    assert round(costs["wholesale_price"], 2) == 2901.12
    assert round(costs["retail_price"], 2) == 5802.24


def test_diamond_carat_zero_forces_zero_cost_even_with_blank_quality():
    """Business rule: diamond_carat == 0 -> diamond_cost == 0, regardless
    of diamond_quality being blank (mirrors B901500-14YS in the data)."""
    costs = calculator.calculate_costs(
        gold_weight_grams=3.9, price_per_gram=42.0,
        diamond_carat=0.0, diamond_cost_per_carat=0.0,
        color_stone_type="Sapphire", color_stone_carat=1.35, color_stone_cost_per_carat=95.0,
        labor_cost=90.0, setting_cost=35.0, markup_percent=200.0,
    )
    assert costs["diamond_cost"] == 0.0


def test_blank_color_stone_type_forces_zero_cost_even_if_carat_given():
    """Business rule: blank color_stone_type -> color_stone_cost == 0,
    even if a carat/cost-per-carat were mistakenly supplied anyway."""
    costs = calculator.calculate_costs(
        gold_weight_grams=5.0, price_per_gram=42.0,
        diamond_carat=0.0, diamond_cost_per_carat=0.0,
        color_stone_type=None, color_stone_carat=5.0, color_stone_cost_per_carat=100.0,
        labor_cost=0.0, setting_cost=0.0, markup_percent=0.0,
    )
    assert costs["color_stone_cost"] == 0.0


def test_biggest_driver_picks_the_largest_bucket():
    costs = {
        "metal_cost": 100.0, "diamond_cost": 500.0, "color_stone_cost": 50.0,
        "labor_cost": 90.0, "setting_cost": 40.0,
    }
    assert calculator.biggest_driver(costs) == "diamond"


def test_biggest_driver_tie_break_prefers_metal_first():
    costs = {
        "metal_cost": 100.0, "diamond_cost": 100.0, "color_stone_cost": 0.0,
        "labor_cost": 0.0, "setting_cost": 0.0,
    }
    assert calculator.biggest_driver(costs) == "metal"


def test_is_lab_grown_detects_lb_prefix():
    assert calculator.is_lab_grown("LB401400-14WVS", "some note") is True


def test_is_lab_grown_detects_note_text():
    assert calculator.is_lab_grown("B999999-14WVS", "Lab grown diamond, special order") is True


def test_is_lab_grown_false_for_natural():
    assert calculator.is_lab_grown("B401400-14WVS", "Natural diamond band in 14K gold") is False


def test_driver_label_natural_vs_lab_grown_diamond():
    natural = calculator.driver_label("diamond", "14K", None, "B401400-14WVS", "Natural diamond band")
    lab = calculator.driver_label("diamond", "14K", None, "LB401400-14WVS", "Lab grown diamond version")
    assert natural == "the natural diamond cost"
    assert lab == "the lab-grown diamond cost"


def test_driver_label_color_stone_uses_stone_name():
    label = calculator.driver_label("color_stone", "18K", "Ruby", "B501200-18YRA", "")
    assert label == "the ruby cost"


def test_round2_rounds_to_two_decimals():
    assert calculator.round2(2901.1234) == 2901.12
    assert calculator.round2(201.6) == 201.6


# ---- Task 1: metal_prices.csv validation ----

def test_build_metal_lookup_rejects_blank_metal_group():
    """Task 1: blank metal_group in metal_prices.csv should raise."""
    rows = [{"metal_group": "", "metal_codes": "14W", "price_per_gram": 42}]
    with pytest.raises(ValueError, match="metal_group.*missing"):
        calculator.build_metal_lookup(rows)


def test_build_metal_lookup_rejects_nan_metal_group():
    """Task 1: NaN metal_group should raise, not silently become 'nan'."""
    rows = [{"metal_group": float("nan"), "metal_codes": "14W", "price_per_gram": 42}]
    with pytest.raises(ValueError, match="metal_group.*missing"):
        calculator.build_metal_lookup(rows)


def test_build_metal_lookup_rejects_nan_price():
    """Task 1: NaN price_per_gram should raise, not silently propagate."""
    rows = [{"metal_group": "14K", "metal_codes": "14W", "price_per_gram": float("nan")}]
    with pytest.raises(ValueError, match="price_per_gram.*not a finite"):
        calculator.build_metal_lookup(rows)


def test_build_metal_lookup_rejects_zero_price():
    """Task 1: price_per_gram must be > 0."""
    rows = [{"metal_group": "14K", "metal_codes": "14W", "price_per_gram": 0}]
    with pytest.raises(ValueError, match="price_per_gram.*must be > 0"):
        calculator.build_metal_lookup(rows)


def test_build_metal_lookup_rejects_negative_price():
    """Task 1: negative price_per_gram should raise."""
    rows = [{"metal_group": "14K", "metal_codes": "14W", "price_per_gram": -5}]
    with pytest.raises(ValueError, match="price_per_gram.*must be > 0"):
        calculator.build_metal_lookup(rows)


def test_build_metal_lookup_rejects_blank_metal_codes():
    """Task 1: blank metal_codes should raise."""
    rows = [{"metal_group": "14K", "metal_codes": "", "price_per_gram": 42}]
    with pytest.raises(ValueError, match="metal_codes.*missing"):
        calculator.build_metal_lookup(rows)


# ---- Task 2: case/whitespace normalization ----

def test_resolve_metal_case_insensitive():
    """Task 2: '14w' should match '14W' in the lookup."""
    group, price, warnings = calculator.resolve_metal("14w", METAL_LOOKUP)
    assert group == "14K"
    assert price == 42.0
    assert warnings == []


def test_resolve_metal_whitespace_tolerant():
    """Task 2: ' 14W ' should match '14W' in the lookup."""
    group, price, warnings = calculator.resolve_metal("  14W  ", METAL_LOOKUP)
    assert group == "14K"
    assert price == 42.0
    assert warnings == []


def test_build_metal_lookup_normalizes_codes_to_uppercase():
    """Task 2: codes in metal_prices.csv are stored uppercase."""
    rows = [{"metal_group": "14K", "metal_codes": "14w, 14y", "price_per_gram": 42}]
    lookup = calculator.build_metal_lookup(rows)
    assert "14W" in lookup
    assert "14Y" in lookup
    assert "14w" not in lookup  # stored normalized
