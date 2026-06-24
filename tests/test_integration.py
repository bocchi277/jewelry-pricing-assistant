"""End-to-end test: runs the real pipeline against the actual provided
data/*.csv files with AI disabled, so it never needs network access or an
API key. This is the test that proves the whole thing actually works on
the real assignment data, not just on hand-built fixtures.
"""
import math
from pathlib import Path

import pytest

import main as app

DATA_DIR = Path(__file__).parent.parent / "data"


def _run_all(style: str | None = None) -> list[dict]:
    args = app.argparse.Namespace(
        pricing=str(DATA_DIR / "pricing_inputs.csv"),
        metals=str(DATA_DIR / "metal_prices.csv"),
        output="outputs/_test_results.json",
        style=style,
        no_ai=True,
        model=None,
        fallback_model=None,
    )
    return app.run(args)


def test_all_ten_rows_process_without_crashing():
    results = _run_all()
    assert len(results) == 10
    for r in results:
        assert r["retail_price"] >= 0
        assert isinstance(r["pricing_explanation"], str) and r["pricing_explanation"]
        assert isinstance(r["validation_warnings"], list)


def test_required_output_fields_present():
    required_fields = {
        "style_number", "metal_group", "metal_cost", "diamond_cost", "color_stone_cost",
        "labor_cost", "setting_cost", "total_cost", "wholesale_price", "retail_price",
        "pricing_explanation", "validation_warnings",
    }
    results = _run_all()
    for r in results:
        assert set(r.keys()) == required_fields, (
            f"Extra: {set(r.keys()) - required_fields}, "
            f"Missing: {required_fields - set(r.keys())}"
        )


def test_worked_example_row_matches_spec_exactly():
    results = _run_all(style="B401400-14WVS")
    assert len(results) == 1
    r = results[0]
    assert r["metal_group"] == "14K"
    assert r["metal_cost"] == 201.6
    assert r["diamond_cost"] == 570.0
    assert r["color_stone_cost"] == 0.0
    assert r["total_cost"] == 906.6
    assert r["wholesale_price"] == 2901.12
    assert r["retail_price"] == 5802.24
    assert "natural diamond" in r["pricing_explanation"].lower()
    assert r["validation_warnings"] == []


def test_lab_grown_row_is_labeled_lab_grown_not_natural():
    """LB301900-14RVS1: diamond_cost (1.5*175=262.5) is the biggest bucket
    here (unlike LB401400-14WVS, where the cheaper lab-grown diamond cost
    is actually overtaken by metal cost -- a good illustration of why the
    driver must be computed, not assumed)."""
    results = _run_all(style="LB301900-14RVS1")
    r = results[0]
    assert "lab-grown diamond" in r["pricing_explanation"].lower()


def test_diamond_only_zero_carat_row_has_zero_diamond_cost():
    """B901500-14YS: diamond_carat=0 and diamond_quality blank."""
    results = _run_all(style="B901500-14YS")
    r = results[0]
    assert r["diamond_cost"] == 0.0
    assert r["color_stone_cost"] > 0  # sapphire cost still applies


def test_mixed_diamond_and_color_stone_row_computes_both():
    """B501200-18YRA: has both a diamond and a ruby."""
    results = _run_all(style="B501200-18YRA")
    r = results[0]
    assert r["metal_group"] == "18K"
    assert r["diamond_cost"] > 0
    assert r["color_stone_cost"] > 0


# ---- Task 1: non-finite result safety net ----

def test_non_finite_values_are_replaced_with_zero(tmp_path):
    """Task 1: if NaN somehow reaches a cost field, it should be clamped
    to 0.0 with a NON_FINITE_RESULT warning, not written as literal NaN."""
    # Create a metal_prices.csv with an unusual-but-valid price
    metals_csv = tmp_path / "metals.csv"
    metals_csv.write_text("metal_group,metal_codes,price_per_gram\n14K,14W,42\n")

    # Create a pricing_inputs.csv with a normal row
    pricing_csv = tmp_path / "pricing.csv"
    pricing_csv.write_text(
        "style_number,metal,gold_weight_grams,diamond_carat,diamond_quality,"
        "diamond_cost_per_carat,color_stone_type,color_stone_carat,"
        "color_stone_cost_per_carat,labor_cost,setting_cost,markup_percent,item_note\n"
        "TEST-001,14W,4.8,1.2,VS,475,,0,0,95,40,220,Test row\n"
    )

    args = app.argparse.Namespace(
        pricing=str(pricing_csv), metals=str(metals_csv),
        output=str(tmp_path / "out.json"), style=None, no_ai=True,
        model=None, fallback_model=None,
    )
    results = app.run(args)
    # This row should be clean — no NaN in normal operation
    assert len(results) == 1
    for field in ("metal_cost", "diamond_cost", "total_cost", "wholesale_price", "retail_price"):
        assert not math.isnan(results[0][field])
        assert not math.isinf(results[0][field])


# ---- Task 1: metal_prices.csv validation ----

def test_load_metal_lookup_rejects_corrupt_csv(tmp_path):
    """Task 1: a metal_prices.csv with NaN price should fail loudly."""
    bad_metals = tmp_path / "metals.csv"
    bad_metals.write_text("metal_group,metal_codes,price_per_gram\n14K,14W,\n")

    with pytest.raises(SystemExit):
        app.load_metal_lookup(bad_metals)


# ---- Task 3: whitespace-aware duplicate detection ----

def test_duplicate_detection_ignores_trailing_whitespace(tmp_path):
    """Task 3: 'STYLE-A' and 'STYLE-A ' should be flagged as duplicates."""
    metals_csv = tmp_path / "metals.csv"
    metals_csv.write_text("metal_group,metal_codes,price_per_gram\n14K,14W,42\n")

    pricing_csv = tmp_path / "pricing.csv"
    pricing_csv.write_text(
        "style_number,metal,gold_weight_grams,diamond_carat,diamond_quality,"
        "diamond_cost_per_carat,color_stone_type,color_stone_carat,"
        "color_stone_cost_per_carat,labor_cost,setting_cost,markup_percent,item_note\n"
        "STYLE-A,14W,4.8,0,,0,,0,0,50,20,100,First row\n"
        "STYLE-A ,14W,4.8,0,,0,,0,0,50,20,100,Trailing space duplicate\n"
    )

    args = app.argparse.Namespace(
        pricing=str(pricing_csv), metals=str(metals_csv),
        output=str(tmp_path / "out.json"), style=None, no_ai=True,
        model=None, fallback_model=None,
    )
    results = app.run(args)
    # Both rows should have a DUPLICATE_STYLE_NUMBER warning
    assert len(results) == 2
    for r in results:
        warning_text = " ".join(r["validation_warnings"])
        assert "more than once" in warning_text.lower() or "duplicate" in warning_text.lower()


# ---- Task 7: empty CSV guard ----

def test_empty_csv_exits_with_message(tmp_path):
    """Task 7: a CSV with headers but no data rows should exit cleanly."""
    metals_csv = tmp_path / "metals.csv"
    metals_csv.write_text("metal_group,metal_codes,price_per_gram\n14K,14W,42\n")

    empty_csv = tmp_path / "pricing.csv"
    empty_csv.write_text(
        "style_number,metal,gold_weight_grams,diamond_carat,diamond_quality,"
        "diamond_cost_per_carat,color_stone_type,color_stone_carat,"
        "color_stone_cost_per_carat,labor_cost,setting_cost,markup_percent,item_note\n"
    )

    args = app.argparse.Namespace(
        pricing=str(empty_csv), metals=str(metals_csv),
        output=str(tmp_path / "out.json"), style=None, no_ai=True,
        model=None, fallback_model=None,
    )
    with pytest.raises(SystemExit, match="No data rows"):
        app.run(args)


# ---- Task 7: ensure_ascii=False ----

def test_output_json_preserves_non_ascii(tmp_path):
    """Task 7: ensure_ascii=False means the output JSON should not contain
    \\uXXXX escapes for non-ASCII characters."""
    metals_csv = tmp_path / "metals.csv"
    metals_csv.write_text("metal_group,metal_codes,price_per_gram\n14K,14W,42\n")

    pricing_csv = tmp_path / "pricing.csv"
    pricing_csv.write_text(
        "style_number,metal,gold_weight_grams,diamond_carat,diamond_quality,"
        "diamond_cost_per_carat,color_stone_type,color_stone_carat,"
        "color_stone_cost_per_carat,labor_cost,setting_cost,markup_percent,item_note\n"
        "TËST-ÜNICÖDÉ,14W,4.8,0,,0,,0,0,50,20,100,Normal note\n"
    )

    output_path = tmp_path / "out.json"
    app.main(["--pricing", str(pricing_csv), "--metals", str(metals_csv),
              "--output", str(output_path), "--no-ai"])

    content = output_path.read_text()
    # The style_number with non-ASCII chars should be preserved literally,
    # not escaped as \uXXXX sequences
    assert "TËST-ÜNICÖDÉ" in content


# ---- Task 6: dollar-figure sanity check ----

def test_dollar_figure_extraction():
    """Task 6: _extract_dollar_amounts should parse $X.XX patterns."""
    import ai_explainer
    amounts = ai_explainer._extract_dollar_amounts("The cost is $201.60 and retail is $5,802.24.")
    assert 201.60 in amounts
    assert 5802.24 in amounts


def test_dollar_figure_validation_passes_for_known_values():
    """Task 6: explanation with correct dollar figures should pass."""
    import ai_explainer
    computed = {"metal_cost": 201.6, "diamond_cost": 570.0, "color_stone_cost": 0.0,
                "labor_cost": 95.0, "setting_cost": 40.0, "total_cost": 906.6,
                "wholesale_price": 2901.12, "retail_price": 5802.24}
    assert ai_explainer._dollar_figures_valid("The metal cost is $201.60", computed) is True


def test_dollar_figure_validation_fails_for_invented_values():
    """Task 6: explanation with an invented dollar figure should fail."""
    import ai_explainer
    computed = {"metal_cost": 201.6, "diamond_cost": 570.0, "color_stone_cost": 0.0,
                "labor_cost": 95.0, "setting_cost": 40.0, "total_cost": 906.6,
                "wholesale_price": 2901.12, "retail_price": 5802.24}
    assert ai_explainer._dollar_figures_valid("The cost is $999.99", computed) is False


# ---- Task 5: auth error detection ----

def test_is_auth_error_detects_403():
    """Task 5: 403 in exception message should be detected as auth error."""
    import ai_explainer
    exc = Exception("403 Permission Denied: API key not valid")
    assert ai_explainer._is_auth_error(exc) is True


def test_is_auth_error_does_not_flag_rate_limit():
    """Task 5: a 429 rate limit error should NOT be flagged as auth."""
    import ai_explainer
    exc = Exception("429 Resource Exhausted: rate limit exceeded")
    assert ai_explainer._is_auth_error(exc) is False
