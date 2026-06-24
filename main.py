#!/usr/bin/env python3
"""CLI entry point for the Jewelry Pricing Assistant.

Usage:
    python main.py
    python main.py --pricing data/pricing_inputs.csv --metals data/metal_prices.csv
    python main.py --no-ai                  # skip Gemini, always use deterministic templates
    python main.py --style B401400-14WVS     # only process one style number
    python main.py --model gemini-2.5-flash        # override primary model for this run
    python main.py --fallback-model gemini-2.0-flash  # override fallback model for this run

Reads the two input CSVs (never modifies them), computes pricing for every
row, and writes a JSON array of results to --output (default
outputs/results.json), printing a one-line summary per item as it goes.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from pathlib import Path

import pandas as pd

import ai_explainer
import calculator
import validator

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; GEMINI_API_KEY can be a real env var instead

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("jewelry_pricing")

REQUIRED_METAL_COLUMNS = {"metal_group", "metal_codes", "price_per_gram"}
REQUIRED_PRICING_COLUMNS = {
    "style_number", "metal", "gold_weight_grams", "diamond_carat", "diamond_quality",
    "diamond_cost_per_carat", "color_stone_type", "color_stone_carat",
    "color_stone_cost_per_carat", "labor_cost", "setting_cost", "markup_percent", "item_note",
}

# Cost/price fields that must be finite numbers in the output.
_NUMERIC_OUTPUT_FIELDS = (
    "metal_cost", "diamond_cost", "color_stone_cost", "labor_cost",
    "setting_cost", "total_cost", "wholesale_price", "retail_price",
)


def load_metal_lookup(path: Path) -> dict:
    df = pd.read_csv(path)
    missing = REQUIRED_METAL_COLUMNS - set(df.columns)
    if missing:
        raise SystemExit(f"{path} is missing required column(s): {sorted(missing)}")
    try:
        return calculator.build_metal_lookup(df.to_dict(orient="records"))
    except ValueError as exc:
        raise SystemExit(str(exc))


def load_pricing_rows(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = REQUIRED_PRICING_COLUMNS - set(df.columns)
    if missing:
        raise SystemExit(f"{path} is missing required column(s): {sorted(missing)}")
    return df


def _clean_row(row: pd.Series) -> dict:
    """Convert a pandas row to a plain dict with NaN -> None, so the rest
    of the pipeline (validator.py, calculator.py) never has to think
    about pandas/NumPy types."""
    clean = {}
    for key, value in row.items():
        if isinstance(value, float) and math.isnan(value):
            clean[key] = None
        else:
            clean[key] = value
    return clean


def _sanitize_non_finite(result: dict, warning_codes: list[tuple[str, dict]]) -> None:
    """Safety net: if any numeric output field is NaN or inf, replace it
    with 0.0 and append a NON_FINITE_RESULT warning. This prevents invalid
    JSON output (json.dumps writes literal NaN/Infinity tokens which are
    not valid JSON and break strict parsers downstream).
    """
    for field in _NUMERIC_OUTPUT_FIELDS:
        value = result.get(field)
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            result[field] = 0.0
            warning_codes.append(("NON_FINITE_RESULT", {"field": field}))


def process_row(raw: dict, metal_lookup: dict, *, use_ai: bool, model: str | None,
                 fallback_model: str | None = None, duplicate_styles: set) -> dict:
    """Compute the full required output for one input row. `raw` is a
    plain dict (already NaN-cleaned), shaped like one pricing_inputs.csv
    row. Never raises -- bad/missing data becomes a validation_warning,
    not a crash, so one bad row never stops the batch.
    """
    warning_codes: list[tuple[str, dict]] = []

    style_number = validator.to_str(raw.get("style_number"))
    if style_number is None:
        warning_codes.append(("MISSING_STYLE_NUMBER", {}))
        style_number = "(missing style_number)"
    elif style_number in duplicate_styles:
        warning_codes.append(("DUPLICATE_STYLE_NUMBER", {"style": style_number}))

    metal_code = validator.to_str(raw.get("metal")) or ""
    metal_group, price_per_gram, metal_warnings = calculator.resolve_metal(metal_code, metal_lookup)
    warning_codes.extend(metal_warnings)

    gold_weight_blank = validator.to_str(raw.get("gold_weight_grams")) is None
    gold_weight, w_gold = validator.to_float(raw.get("gold_weight_grams"), "gold_weight_grams")

    diamond_cpc_blank = validator.to_str(raw.get("diamond_cost_per_carat")) is None
    diamond_carat, w_dcarat = validator.to_float(raw.get("diamond_carat"), "diamond_carat")
    diamond_cost_per_carat, w_dcpc = validator.to_float(raw.get("diamond_cost_per_carat"), "diamond_cost_per_carat")

    color_stone_cpc_blank = validator.to_str(raw.get("color_stone_cost_per_carat")) is None
    color_stone_carat, w_ccarat = validator.to_float(raw.get("color_stone_carat"), "color_stone_carat")
    color_stone_cost_per_carat, w_ccpc = validator.to_float(
        raw.get("color_stone_cost_per_carat"), "color_stone_cost_per_carat"
    )
    labor_cost, w_labor = validator.to_float(raw.get("labor_cost"), "labor_cost")
    setting_cost, w_setting = validator.to_float(raw.get("setting_cost"), "setting_cost")

    markup_blank = validator.to_str(raw.get("markup_percent")) is None
    markup_percent, w_markup = validator.to_float(raw.get("markup_percent"), "markup_percent")

    for w in (w_gold, w_dcarat, w_dcpc, w_ccarat, w_ccpc, w_labor, w_setting, w_markup):
        if w:
            warning_codes.append(w)
    if markup_blank:
        warning_codes.append(("MISSING_MARKUP", {}))

    diamond_quality = validator.to_str(raw.get("diamond_quality"))
    color_stone_type = validator.to_str(raw.get("color_stone_type"))
    item_note = validator.to_str(raw.get("item_note")) or ""

    warning_codes.extend(
        validator.cross_field_warnings(
            diamond_carat, diamond_quality, diamond_cpc_blank,
            color_stone_type, color_stone_carat, color_stone_cpc_blank,
            metal_code, gold_weight, gold_weight_blank,
            diamond_cost_per_carat=diamond_cost_per_carat,
            color_stone_cost_per_carat=color_stone_cost_per_carat,
            metal_resolved=(metal_group is not None),
        )
    )

    costs = calculator.calculate_costs(
        gold_weight_grams=gold_weight,
        price_per_gram=price_per_gram,
        diamond_carat=diamond_carat,
        diamond_cost_per_carat=diamond_cost_per_carat,
        color_stone_type=color_stone_type,
        color_stone_carat=color_stone_carat,
        color_stone_cost_per_carat=color_stone_cost_per_carat,
        labor_cost=labor_cost,
        setting_cost=setting_cost,
        markup_percent=markup_percent,
    )

    driver = calculator.biggest_driver(costs)
    label = calculator.driver_label(driver, metal_group, color_stone_type, style_number, item_note)

    computed = {
        "style_number": style_number,
        "metal_group": metal_group or "UNKNOWN",
        "markup_percent": markup_percent,
        **costs,
    }

    ai_result = ai_explainer.generate_explanation(
        computed, label, warning_codes, use_ai=use_ai, model=model, fallback_model=fallback_model,
    )

    result = {
        "style_number": style_number,
        "metal_group": metal_group or "UNKNOWN",
        "metal_cost": calculator.round2(costs["metal_cost"]),
        "diamond_cost": calculator.round2(costs["diamond_cost"]),
        "color_stone_cost": calculator.round2(costs["color_stone_cost"]),
        "labor_cost": calculator.round2(costs["labor_cost"]),
        "setting_cost": calculator.round2(costs["setting_cost"]),
        "total_cost": calculator.round2(costs["total_cost"]),
        "wholesale_price": calculator.round2(costs["wholesale_price"]),
        "retail_price": calculator.round2(costs["retail_price"]),
        "pricing_explanation": ai_result["pricing_explanation"],
        "validation_warnings": ai_result["validation_warnings"],
    }

    # Task 1 safety net: replace any NaN/inf in output with 0.0 + warning
    _sanitize_non_finite(result, warning_codes)
    # If non-finite warnings were added, re-render the warnings in the result
    if any(code == "NON_FINITE_RESULT" for code, _ in warning_codes):
        result["validation_warnings"] = validator.render_warnings(warning_codes)

    return result


def run(args: argparse.Namespace) -> list[dict]:
    pricing_path = Path(args.pricing)
    metals_path = Path(args.metals)
    if not pricing_path.exists():
        raise SystemExit(f"Pricing input file not found: {pricing_path}")
    if not metals_path.exists():
        raise SystemExit(f"Metal prices file not found: {metals_path}")

    metal_lookup = load_metal_lookup(metals_path)
    df = load_pricing_rows(pricing_path)

    if args.style:
        df = df[df["style_number"] == args.style]
        if df.empty:
            raise SystemExit(f"No row found with style_number == {args.style}")

    # Task 7: fail fast on empty CSV
    if df.empty:
        raise SystemExit(
            f"No data rows found in {pricing_path}. "
            "The file has column headers but no pricing data to process."
        )

    # Task 3: strip whitespace before computing duplicates so that
    # "B401400-14WVS" and "B401400-14WVS " are treated as the same style.
    stripped_styles = df["style_number"].astype(str).str.strip()
    style_counts = stripped_styles.value_counts()
    duplicate_styles = set(style_counts[style_counts > 1].index)

    use_ai = not args.no_ai
    if use_ai and not os.getenv("GEMINI_API_KEY"):
        logger.warning(
            "⚠️  No GEMINI_API_KEY found. AI-generated explanations are disabled.\n"
            "    The tool will use deterministic template text instead.\n"
            "    To enable AI: copy .env.example to .env and add your API key,\n"
            "    or set the GEMINI_API_KEY environment variable.\n"
            "    To silence this warning: pass --no-ai explicitly."
        )

    results = []
    for _, row in df.iterrows():
        raw = _clean_row(row)
        result = process_row(
            raw, metal_lookup, use_ai=use_ai, model=args.model,
            fallback_model=args.fallback_model,
            duplicate_styles=duplicate_styles,
        )
        results.append(result)
        warn_suffix = (
            f"  [{len(result['validation_warnings'])} warning(s)]" if result["validation_warnings"] else ""
        )
        logger.info("%-20s -> retail $%.2f%s", result["style_number"], result["retail_price"], warn_suffix)

    return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Jewelry Pricing Assistant")
    parser.add_argument("--pricing", default="data/pricing_inputs.csv", help="Path to pricing_inputs.csv")
    parser.add_argument("--metals", default="data/metal_prices.csv", help="Path to metal_prices.csv")
    parser.add_argument("--output", default="outputs/results.json", help="Where to write the JSON results")
    parser.add_argument("--style", default=None, help="Only process this one style_number")
    parser.add_argument("--no-ai", action="store_true", help="Skip Gemini entirely; always use deterministic text")
    parser.add_argument("--model", default=None, help="Override the primary model (GEMINI_MODEL) for this run")
    parser.add_argument(
        "--fallback-model", default=None,
        help="Override the fallback model (GEMINI_FALLBACK_MODEL) for this run",
    )
    args = parser.parse_args(argv)

    results = run(args)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Task 7: ensure_ascii=False so non-ASCII chars stay readable;
    # wrap in try/except for clear error on unwritable paths.
    try:
        output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    except (OSError, IOError) as exc:
        raise SystemExit(f"Cannot write output file '{output_path}': {exc}")

    logger.info("Wrote %d result(s) to %s", len(results), output_path)


if __name__ == "__main__":
    main(sys.argv[1:])
