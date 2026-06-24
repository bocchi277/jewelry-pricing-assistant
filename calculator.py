"""Deterministic pricing calculations for the Jewelry Pricing Assistant.

Every function in this file is pure: the same inputs always produce the
same outputs, there is no AI, no randomness, and no row-specific
special-casing. This is intentional -- it is the half of the assignment
that must be 100% traceable to an input or formula.
"""
from __future__ import annotations

import math

# Priority order used only to break EXACT ties between cost buckets when
# picking the "biggest cost driver." Metal wins ties, then diamond, then
# color stone, then labor, then setting. This keeps the result fully
# deterministic instead of depending on dict ordering or float noise.
_DRIVER_PRIORITY = ["metal", "diamond", "color_stone", "labor", "setting"]


def resolve_metal(metal_code: str, metal_lookup: dict) -> tuple[str | None, float, list[tuple[str, dict]]]:
    """Look up (metal_group, price_per_gram) for a metal code such as '14W'.

    Returns (metal_group, price_per_gram, warning_codes). An unknown or
    missing code never raises -- it returns price 0.0 plus a warning code,
    so one bad row never crashes the whole batch.

    Metal codes are normalized to uppercase with whitespace stripped, so
    '14w', ' 14W ', and '14W' all match the same lookup entry.
    """
    code = (metal_code or "").strip().upper()
    if not code:
        return None, 0.0, [("MISSING_METAL_CODE", {})]
    if code not in metal_lookup:
        return None, 0.0, [("UNKNOWN_METAL_CODE", {"code": code})]
    group, price = metal_lookup[code]
    return group, price, []


def build_metal_lookup(metal_rows: list[dict]) -> dict[str, tuple[str, float]]:
    """Build a {metal_code: (metal_group, price_per_gram)} lookup from rows
    shaped like metal_prices.csv, where `metal_codes` is a comma-separated
    string such as "14W,14Y,14R".

    Validates every row: metal_group and metal_codes must be non-blank
    strings, and price_per_gram must be a finite number > 0.  A bad row
    raises ValueError -- metal_prices.csv is small reference data and
    should fail loudly rather than silently build a corrupt lookup.

    Metal codes are normalized to uppercase with whitespace stripped so
    that lookups are case- and whitespace-insensitive.
    """
    lookup: dict[str, tuple[str, float]] = {}
    for i, row in enumerate(metal_rows, start=1):
        raw_group = row.get("metal_group")
        raw_codes = row.get("metal_codes")
        raw_price = row.get("price_per_gram")

        # Validate metal_group
        group = _validated_str(raw_group, "metal_group", i)
        # Validate metal_codes
        codes_str = _validated_str(raw_codes, "metal_codes", i)
        # Validate price_per_gram
        price = _validated_positive_float(raw_price, "price_per_gram", i)

        for code in codes_str.split(","):
            code = code.strip().upper()
            if code:
                lookup[code] = (group, price)
    return lookup


def _validated_str(value, column: str, row_num: int) -> str:
    """Ensure a metal_prices.csv cell is a non-blank string."""
    if value is None:
        raise ValueError(
            f"metal_prices.csv row {row_num}: '{column}' is missing/blank."
        )
    s = str(value).strip()
    if not s or s.lower() == "nan":
        raise ValueError(
            f"metal_prices.csv row {row_num}: '{column}' is missing/blank."
        )
    return s


def _validated_positive_float(value, column: str, row_num: int) -> float:
    """Ensure a metal_prices.csv cell is a finite number > 0."""
    if value is None:
        raise ValueError(
            f"metal_prices.csv row {row_num}: '{column}' is missing/blank."
        )
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"metal_prices.csv row {row_num}: '{column}' has non-numeric value '{value}'."
        )
    if math.isnan(f) or math.isinf(f):
        raise ValueError(
            f"metal_prices.csv row {row_num}: '{column}' is not a finite number ({value})."
        )
    if f <= 0:
        raise ValueError(
            f"metal_prices.csv row {row_num}: '{column}' must be > 0, got {f}."
        )
    return f


def calculate_costs(
    *,
    gold_weight_grams: float,
    price_per_gram: float,
    diamond_carat: float,
    diamond_cost_per_carat: float,
    color_stone_type: str | None,
    color_stone_carat: float,
    color_stone_cost_per_carat: float,
    labor_cost: float,
    setting_cost: float,
    markup_percent: float,
) -> dict:
    """Apply the required formulas from the assignment spec.

    All inputs are expected to already be clean numbers (see validator.py
    for coercion/defaulting of raw CSV cells) -- the only defaulting done
    here is the two explicit business rules below. Nothing is rounded
    until the very end (round2()), so intermediate precision is preserved.
    """
    metal_cost = gold_weight_grams * price_per_gram

    # Business rule: diamond_carat == 0 -> diamond_cost is 0, full stop,
    # even if diamond_quality is blank or diamond_cost_per_carat is missing.
    if diamond_carat == 0:
        diamond_cost = 0.0
    else:
        diamond_cost = diamond_carat * diamond_cost_per_carat

    # Business rule: blank color_stone_type -> color_stone_cost is 0, even
    # if a carat weight or cost-per-carat was (incorrectly) supplied anyway.
    has_color_stone = bool(color_stone_type and str(color_stone_type).strip())
    if not has_color_stone:
        color_stone_cost = 0.0
    else:
        color_stone_cost = color_stone_carat * color_stone_cost_per_carat

    total_cost = metal_cost + diamond_cost + color_stone_cost + labor_cost + setting_cost
    wholesale_price = total_cost * (1 + markup_percent / 100)
    retail_price = wholesale_price * 2

    return {
        "metal_cost": metal_cost,
        "diamond_cost": diamond_cost,
        "color_stone_cost": color_stone_cost,
        "labor_cost": labor_cost,
        "setting_cost": setting_cost,
        "total_cost": total_cost,
        "wholesale_price": wholesale_price,
        "retail_price": retail_price,
    }


def biggest_driver(costs: dict) -> str:
    """Return which of the 5 cost buckets is largest: one of "metal",
    "diamond", "color_stone", "labor", "setting". Ties are broken by
    _DRIVER_PRIORITY so the result never depends on float noise or
    dict ordering.
    """
    bucket_values = {
        "metal": costs["metal_cost"],
        "diamond": costs["diamond_cost"],
        "color_stone": costs["color_stone_cost"],
        "labor": costs["labor_cost"],
        "setting": costs["setting_cost"],
    }
    max_value = max(bucket_values.values())
    for name in _DRIVER_PRIORITY:
        if bucket_values[name] == max_value:
            return name
    return _DRIVER_PRIORITY[0]  # unreachable; keeps the function total


def is_lab_grown(style_number: str, item_note: str | None) -> bool:
    """Deterministic heuristic for natural vs. lab-grown diamonds.

    The assignment's own example output phrases this as "the natural
    diamond cost," so the explanation needs to know which kind it is. This
    is decided here -- never guessed by the AI -- from two signals already
    present in the data: an "LB" style-number prefix, or the words
    "lab grown" in the item note.
    """
    note = (item_note or "").lower()
    code = (style_number or "").strip().upper()
    return code.startswith("LB") or "lab grown" in note


def driver_label(
    driver: str,
    metal_group: str | None,
    color_stone_type: str | None,
    style_number: str,
    item_note: str | None,
) -> str:
    """Human-readable label for the biggest cost driver, fully computed
    here so the AI step only has to use the phrase, never invent it."""
    if driver == "metal":
        return f"the metal cost ({metal_group or 'unknown metal'})"
    if driver == "diamond":
        kind = "lab-grown" if is_lab_grown(style_number, item_note) else "natural"
        return f"the {kind} diamond cost"
    if driver == "color_stone":
        stone = (color_stone_type or "color stone").strip().lower()
        return f"the {stone} cost"
    if driver == "labor":
        return "the labor cost"
    return "the setting cost"


def round2(value: float) -> float:
    """Round only for display. Never call this mid-calculation -- the spec
    explicitly says to avoid early rounding."""
    return round(value, 2)
