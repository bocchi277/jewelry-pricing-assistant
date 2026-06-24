"""Deterministic input validation, coercion, and warning-message rendering.

Design note: this module only decides WHETHER a warning condition exists
(e.g. "this metal code isn't in metal_prices.csv") and produces a
machine-readable (code, params) pair for it. The English wording can come
from here (render_warning, used as the deterministic fallback) or be
rephrased by the AI layer in ai_explainer.py -- either way, the *decision*
that something is wrong is made here, deterministically, never by the AI.
This keeps "Use AI to generate plain-English ... warnings" and "keep
calculations deterministic" both true at once.
"""
from __future__ import annotations

import math

WarningCode = tuple[str, dict]

# Every warning this tool can raise, with a template for its plain-English
# rendering. Used both as the offline fallback and as the "ground truth"
# the AI is asked to rephrase (never invent new ones beyond this set).
WARNING_TEMPLATES: dict[str, str] = {
    "MISSING_STYLE_NUMBER": "This row has no style_number.",
    "DUPLICATE_STYLE_NUMBER": "style_number '{style}' appears more than once in the input file.",
    "MISSING_METAL_CODE": "No metal code was provided; metal cost was set to $0.00.",
    "UNKNOWN_METAL_CODE": "Metal code '{code}' was not found in metal_prices.csv; metal cost was set to $0.00.",
    "MISSING_DIAMOND_QUALITY": "A diamond carat weight was provided without a diamond quality grade.",
    "COLOR_STONE_CARAT_WITHOUT_TYPE": (
        "A color stone carat weight of {carat} was provided but no color stone type was "
        "specified; color stone cost was set to $0.00 per the business rule."
    ),
    "NEGATIVE_VALUE_CLAMPED": "Field '{field}' contained a negative value ({value}) and was clamped to 0.",
    "NON_NUMERIC_VALUE": "Field '{field}' contained a non-numeric value ('{raw}') and was treated as 0.",
    "MISSING_MARKUP": "markup_percent was missing or blank; treated as a 0% markup.",
    "MISSING_DIAMOND_COST_PER_CARAT": "A diamond carat weight was provided without a cost per carat; diamond cost was set to $0.00.",
    "MISSING_COLOR_STONE_COST_PER_CARAT": "A color stone carat weight was provided without a cost per carat; color stone cost was set to $0.00.",
    "COLOR_STONE_TYPE_WITHOUT_CARAT": "A color stone type ('{type}') was provided but the carat weight is missing or zero; color stone cost was set to $0.00.",
    "MISSING_GOLD_WEIGHT": "A metal code was provided but the gold weight is missing; metal cost was set to $0.00.",
    "ZERO_GOLD_WEIGHT": (
        "gold_weight_grams is 0 with a valid metal code; metal cost is $0.00. "
        "This may be intentional for a loose-stone item."
    ),
    "ZERO_COST_PER_CARAT": (
        "{stone_type} carat weight is {carat} but cost per carat is $0.00; "
        "{stone_type} cost will be $0.00."
    ),
    "NON_FINITE_RESULT": (
        "A computed price field ('{field}') produced a non-finite value and was "
        "replaced with $0.00. This usually indicates corrupt reference data."
    ),
}


def _is_blank(raw) -> bool:
    if raw is None:
        return True
    if isinstance(raw, float) and math.isnan(raw):
        return True
    return str(raw).strip() == ""


def to_str(raw) -> str | None:
    """Coerce a CSV cell to a stripped string, or None if it's blank."""
    if _is_blank(raw):
        return None
    return str(raw).strip()


def to_float(raw, field_name: str, *, default: float = 0.0, allow_negative: bool = False) -> tuple[float, WarningCode | None]:
    """Coerce a CSV cell to float. Never raises -- bad data becomes
    `default` plus a warning code instead of crashing the row.

    Returns (value, warning_or_None).
    """
    if _is_blank(raw):
        return default, None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default, ("NON_NUMERIC_VALUE", {"field": field_name, "raw": str(raw)})
    if value < 0 and not allow_negative:
        return 0.0, ("NEGATIVE_VALUE_CLAMPED", {"field": field_name, "value": value})
    return value, None


def cross_field_warnings(
    diamond_carat: float,
    diamond_quality: str | None,
    diamond_cpc_blank: bool,
    color_stone_type: str | None,
    color_stone_carat: float,
    color_stone_cpc_blank: bool,
    metal_code: str,
    gold_weight: float,
    gold_weight_blank: bool,
    *,
    diamond_cost_per_carat: float | None = None,
    color_stone_cost_per_carat: float | None = None,
    metal_resolved: bool = False,
) -> list[WarningCode]:
    """Warnings that depend on more than one field at once.

    The three keyword-only args are optional so that existing call sites
    and tests continue to work unchanged; the new Task 4 warnings only
    fire when they're explicitly passed.
    """
    warnings: list[WarningCode] = []
    if diamond_carat > 0 and not diamond_quality:
        warnings.append(("MISSING_DIAMOND_QUALITY", {}))
    if diamond_carat > 0 and diamond_cpc_blank:
        warnings.append(("MISSING_DIAMOND_COST_PER_CARAT", {}))

    if color_stone_carat > 0 and not color_stone_type:
        warnings.append(("COLOR_STONE_CARAT_WITHOUT_TYPE", {"carat": color_stone_carat}))
    if color_stone_type and color_stone_carat <= 0:
        warnings.append(("COLOR_STONE_TYPE_WITHOUT_CARAT", {"type": color_stone_type}))
    if color_stone_carat > 0 and color_stone_cpc_blank:
        warnings.append(("MISSING_COLOR_STONE_COST_PER_CARAT", {}))

    # MISSING_GOLD_WEIGHT: metal code provided but gold weight is blank
    if metal_code and gold_weight_blank:
        warnings.append(("MISSING_GOLD_WEIGHT", {}))

    # ZERO_GOLD_WEIGHT: gold weight is explicitly 0 with a valid metal code
    # (Task 4 — informational only, does not change any cost)
    if metal_resolved and gold_weight == 0 and not gold_weight_blank:
        warnings.append(("ZERO_GOLD_WEIGHT", {}))

    # ZERO_COST_PER_CARAT: carat weight > 0 but cost-per-carat is $0
    # (Task 4 — informational only, does not change any cost)
    if diamond_cost_per_carat is not None and diamond_carat > 0 and diamond_cost_per_carat == 0 and not diamond_cpc_blank:
        warnings.append(("ZERO_COST_PER_CARAT", {"stone_type": "Diamond", "carat": diamond_carat}))
    if color_stone_cost_per_carat is not None and color_stone_carat > 0 and color_stone_cost_per_carat == 0 and not color_stone_cpc_blank:
        warnings.append(("ZERO_COST_PER_CARAT", {"stone_type": "Color stone", "carat": color_stone_carat}))

    return warnings


def render_warning(code: str, params: dict) -> str:
    """Deterministic plain-English rendering of a warning code. Used as the
    offline fallback when AI is unavailable/disabled/fails."""
    template = WARNING_TEMPLATES.get(code, code)
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template


def render_warnings(warning_codes: list[WarningCode]) -> list[str]:
    return [render_warning(code, params) for code, params in warning_codes]
