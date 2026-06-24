"""AI layer: turns already-computed, deterministic numbers into plain
English. This is the ONLY file in the project that talks to an AI model.

Why it's isolated here:
- calculator.py and validator.py never import this module, so the
  deterministic math can be tested, trusted, and run completely without
  it (see `--no-ai` in main.py).
- Swapping the Gemini model version, or even swapping providers entirely
  (Gemini -> OpenAI -> Anthropic -> a local model), only ever requires
  editing `_call_gemini()` below. Nothing in calculator.py, validator.py,
  or main.py needs to change, because they only ever see the plain dict
  returned by `generate_explanation()`.

Model configuration (env vars or CLI flags -- no code changes needed):
    GEMINI_API_KEY        required to actually call the AI
    GEMINI_MODEL          primary model; defaults to "gemini-3.1-flash-lite"
    GEMINI_FALLBACK_MODEL secondary model tried when the primary fails;
                          defaults to "gemini-2.5-flash"

Rate-limit handling:
    Free-tier Gemini models have per-minute request caps (e.g. 5 req/min
    for gemini-2.5-flash). This module handles that two ways:
    1. Proactive throttling -- a RateThrottler tracks recent request
       timestamps per model and sleeps *before* sending a request that
       would exceed the limit, avoiding 429 errors entirely.
    2. Reactive 429 detection -- if a rate-limit error does slip through,
       the retry loop detects it and sleeps for the server-suggested or
       default cooldown instead of the generic exponential backoff.

Fallback cascade:
    primary model → fallback model → deterministic templates
    If both models fail (rate-limited, network error, bad parse, etc.),
    the tool still produces a complete, valid result using f-string
    templates. The AI is a best-effort enhancement, never a single
    point of failure.

Auth-error fast-fail (Task 5):
    If the first Gemini call of a run returns a 401/403/PERMISSION_DENIED
    error (invalid or revoked API key), AI is disabled for the rest of
    that run. This avoids wasting 30 retries (3 per row × 10 rows) and
    the associated wall-clock delay.

Dollar-figure sanity check (Task 6):
    After getting pricing_explanation from Gemini, any $X.XX patterns in
    the text are extracted and verified against the computed values. If
    Gemini stated a dollar amount that doesn't match any value it was
    given, the AI explanation is discarded and the deterministic fallback
    is used instead.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict

from pydantic import BaseModel

import validator

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")
MAX_RETRIES = 3

# Default rate limits (requests per minute) per model. Conservative
# defaults -- if a model actually allows more, it just means we
# over-throttle slightly (never under-throttle).
_DEFAULT_RPM: dict[str, int] = {
    "gemini-2.5-flash": 5,
    "gemini-3.1-flash-lite": 10,
}
_GLOBAL_DEFAULT_RPM = 10  # fallback for models not listed above

# Module-level flag: set to True when a 401/403/PERMISSION_DENIED error
# is detected, disabling AI for the rest of the run so we don't waste
# retries on every subsequent row.
_ai_disabled_due_to_auth = False


# ---------------------------------------------------------------------------
# Rate Throttler
# ---------------------------------------------------------------------------

class RateThrottler:
    """Proactive per-model rate limiter.

    Tracks request timestamps in a sliding 60-second window. Before each
    request, checks whether firing now would exceed the model's RPM cap.
    If so, sleeps exactly long enough for the oldest request in the window
    to fall outside it -- avoiding a 429 error entirely rather than
    reacting to one after the fact.

    Thread-safety note: this project processes rows sequentially, so no
    locking is needed. If parallelism is added later, wrap `wait()` in a
    threading.Lock per model.
    """

    def __init__(self) -> None:
        # {model_name: [timestamp, timestamp, ...]}
        self._history: dict[str, list[float]] = defaultdict(list)

    def _rpm_for(self, model: str) -> int:
        """Look up the RPM cap for a model, falling back to the global default."""
        return _DEFAULT_RPM.get(model, _GLOBAL_DEFAULT_RPM)

    def wait(self, model: str) -> None:
        """Sleep if needed so the next request won't exceed the rate limit."""
        rpm = self._rpm_for(model)
        now = time.time()
        window_start = now - 60.0

        # Prune timestamps older than the 60-second window
        history = self._history[model]
        self._history[model] = [ts for ts in history if ts > window_start]
        history = self._history[model]

        if len(history) >= rpm:
            # The oldest request in the window determines when we can send again
            oldest = history[0]
            sleep_seconds = (oldest + 60.0) - now + 0.1  # +0.1s safety margin
            if sleep_seconds > 0:
                logger.info(
                    "Rate throttle: %s has %d requests in the last 60s (limit %d). "
                    "Sleeping %.1fs before next request.",
                    model, len(history), rpm, sleep_seconds,
                )
                time.sleep(sleep_seconds)

    def record(self, model: str) -> None:
        """Record that a request was just sent for this model."""
        self._history[model].append(time.time())


# Module-level throttler instance, shared across all calls in a run.
_throttler = RateThrottler()


# ---------------------------------------------------------------------------
# Pydantic schema & helpers
# ---------------------------------------------------------------------------

class _ExplanationSchema(BaseModel):
    """Forces Gemini to return exactly this shape via response_schema,
    instead of hoping it follows free-text JSON instructions."""

    pricing_explanation: str
    validation_warnings: list[str]


def _fallback_explanation(driver_label: str, metal_group: str | None, markup_percent: float) -> str:
    return (
        f"The largest cost driver is {driver_label}. "
        f"This {metal_group or 'item'} piece uses a {markup_percent:.0f}% wholesale markup."
    )


def _build_prompt(computed: dict, driver_label: str, warning_lines: list[str]) -> str:
    warnings_block = (
        "\n".join(f"- {line}" for line in warning_lines)
        if warning_lines
        else "(none -- this item has no warnings; return an empty list)"
    )
    return f"""You write plain-English text for a jewelry pricing report.

All numbers and facts below are FINAL, already computed by deterministic
formulas. Never change, recompute, or round them differently. Never state
a dollar amount, fact, or warning that is not given to you below.

Computed data for style {computed['style_number']}:
- metal_group: {computed['metal_group']}
- metal_cost: {computed['metal_cost']:.2f}
- diamond_cost: {computed['diamond_cost']:.2f}
- color_stone_cost: {computed['color_stone_cost']:.2f}
- labor_cost: {computed['labor_cost']:.2f}
- setting_cost: {computed['setting_cost']:.2f}
- total_cost: {computed['total_cost']:.2f}
- markup_percent: {computed['markup_percent']:.2f}
- wholesale_price: {computed['wholesale_price']:.2f}
- retail_price: {computed['retail_price']:.2f}
- biggest cost driver (already determined -- just state this, don't recompute it): {driver_label}

Warning facts already detected by validation logic (rewrite each as ONE
clear, friendly sentence; do not add, remove, merge, or invent any):
{warnings_block}

Return:
1. pricing_explanation: 1-2 sentences naming "{driver_label}" as the
   primary cost driver. You may reference metal_group and markup_percent
   for context. Do not mention any dollar figure not listed above.
2. validation_warnings: a list with exactly {len(warning_lines)}
   sentence(s), one rewritten sentence per warning fact above, same order.
"""


# ---------------------------------------------------------------------------
# 429 / auth-error detection
# ---------------------------------------------------------------------------

def _is_rate_limit_error(exc: Exception) -> bool:
    """Check whether an exception is a 429 / rate-limit error.

    Gemini SDK raises google.api_core.exceptions.ResourceExhausted (gRPC)
    or a ClientError with status 429 (REST). We pattern-match on common
    signals so this works even if the SDK version changes its exception
    hierarchy.
    """
    exc_type = type(exc).__name__
    exc_str = str(exc).lower()
    return (
        "resourceexhausted" in exc_type.lower()
        or "429" in exc_str
        or "rate limit" in exc_str
        or "quota" in exc_str
    )


def _is_auth_error(exc: Exception) -> bool:
    """Check whether an exception is an authentication/permission error.

    Detects 401 Unauthenticated, 403 PermissionDenied, and common error
    messages from the Gemini SDK indicating an invalid or revoked API key.
    """
    exc_type = type(exc).__name__.lower()
    exc_str = str(exc).lower()
    return (
        "permissiondenied" in exc_type
        or "unauthenticated" in exc_type
        or "401" in exc_str
        or "403" in exc_str
        or "permission_denied" in exc_str
        or "api key not valid" in exc_str
        or "api_key_invalid" in exc_str
    )


def _extract_retry_after(exc: Exception) -> float | None:
    """Try to extract a Retry-After hint (in seconds) from the error.

    Some 429 responses include a Retry-After header or a message like
    'retry after 12s'. Returns None if no hint is found.
    """
    match = re.search(r"retry.?after[:\s]*(\d+)", str(exc), re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


# ---------------------------------------------------------------------------
# Dollar-figure sanity check (Task 6)
# ---------------------------------------------------------------------------

def _extract_dollar_amounts(text: str) -> list[float]:
    """Extract all $<number> patterns from a string.

    Matches patterns like $201.60, $5,802.24, $0.00, etc.
    """
    matches = re.findall(r"\$[\d,]+(?:\.\d+)?", text)
    amounts = []
    for m in matches:
        try:
            amounts.append(float(m.replace("$", "").replace(",", "")))
        except ValueError:
            pass
    return amounts


def _dollar_figures_valid(explanation: str, computed: dict) -> bool:
    """Check that every dollar figure in the AI explanation matches a
    computed value (within ±$0.01 for rounding tolerance).

    Returns True if all figures check out, or if no dollar figures were
    mentioned at all. Returns False if any invented figure is found.
    """
    mentioned = _extract_dollar_amounts(explanation)
    if not mentioned:
        return True

    known_values = {
        round(computed.get("metal_cost", 0), 2),
        round(computed.get("diamond_cost", 0), 2),
        round(computed.get("color_stone_cost", 0), 2),
        round(computed.get("labor_cost", 0), 2),
        round(computed.get("setting_cost", 0), 2),
        round(computed.get("total_cost", 0), 2),
        round(computed.get("wholesale_price", 0), 2),
        round(computed.get("retail_price", 0), 2),
    }

    for amount in mentioned:
        if not any(abs(amount - known) <= 0.01 for known in known_values):
            logger.warning(
                "AI explanation mentions $%.2f which doesn't match any computed value — "
                "discarding AI explanation and using deterministic fallback.",
                amount,
            )
            return False
    return True


# ---------------------------------------------------------------------------
# Gemini API call (with retries + 429 handling + auth fast-fail)
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str, model: str, api_key: str) -> dict:
    """The only function in the project that knows about the Gemini SDK.
    Returns a dict shaped like _ExplanationSchema. Any other provider's
    client call can replace the body of this function without touching
    anything else.

    Raises RuntimeError on persistent failure, or AuthError (a plain
    RuntimeError subclass) on auth/permission failure so the caller can
    disable AI for the rest of the run.
    """
    global _ai_disabled_due_to_auth

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        # Proactive throttle: sleep if we're at the rate limit
        _throttler.wait(model)
        try:
            _throttler.record(model)
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_ExplanationSchema,
                ),
            )
            return json.loads(response.text)
        except Exception as exc:  # noqa: BLE001 - any SDK/network error should fall back, not crash
            last_error = exc

            # Auth errors: fail fast, don't retry
            if _is_auth_error(exc):
                logger.warning(
                    "Authentication/permission error on %s: %s — "
                    "disabling AI for the rest of this run.",
                    model, exc,
                )
                _ai_disabled_due_to_auth = True
                raise RuntimeError(f"Auth error on {model}: {exc}") from exc

            if _is_rate_limit_error(exc):
                # 429: use server hint or default 60s cooldown
                retry_after = _extract_retry_after(exc) or 60.0
                logger.warning(
                    "Rate-limited on %s (attempt %s/%s): %s -- sleeping %.0fs",
                    model, attempt + 1, MAX_RETRIES, exc, retry_after,
                )
                time.sleep(retry_after)
            else:
                # Generic error: exponential backoff
                wait_seconds = 2 ** attempt
                logger.warning(
                    "Gemini call failed on %s (attempt %s/%s): %s -- retrying in %ss",
                    model, attempt + 1, MAX_RETRIES, exc, wait_seconds,
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(wait_seconds)

    raise RuntimeError(f"Gemini call failed on {model} after {MAX_RETRIES} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reset_auth_state() -> None:
    """Reset the module-level auth-disabled flag.

    Called between runs (e.g. in tests) so a previous auth failure doesn't
    bleed into a fresh run.
    """
    global _ai_disabled_due_to_auth
    _ai_disabled_due_to_auth = False


def generate_explanation(
    computed: dict,
    driver_label: str,
    warning_codes: list[tuple[str, dict]],
    *,
    use_ai: bool = True,
    model: str | None = None,
    fallback_model: str | None = None,
) -> dict:
    """Returns {"pricing_explanation": str, "validation_warnings": list[str]}.

    Model cascade:
        1. Try the primary model (--model flag > GEMINI_MODEL env > gemini-3.1-flash-lite)
        2. If that fails, try the fallback model (--fallback-model flag >
           GEMINI_FALLBACK_MODEL env > gemini-2.5-flash)
        3. If that also fails, use deterministic f-string templates

    Always returns something usable -- never raises up to main.py.
    """
    global _ai_disabled_due_to_auth

    fallback_warnings = validator.render_warnings(warning_codes)
    fallback = {
        "pricing_explanation": _fallback_explanation(
            driver_label, computed.get("metal_group"), computed.get("markup_percent", 0.0)
        ),
        "validation_warnings": fallback_warnings,
    }

    api_key = os.getenv("GEMINI_API_KEY")
    if not use_ai or not api_key:
        return fallback

    # Fast-fail: if a previous call in this run hit an auth error, skip AI
    if _ai_disabled_due_to_auth:
        return fallback

    prompt = _build_prompt(computed, driver_label, fallback_warnings)

    # Build the ordered list of models to try
    primary = model or DEFAULT_MODEL
    fb = fallback_model or FALLBACK_MODEL
    models_to_try = [primary]
    if fb and fb != primary:
        models_to_try.append(fb)

    for i, current_model in enumerate(models_to_try):
        try:
            result = _call_gemini(prompt, current_model, api_key)
            explanation = str(result.get("pricing_explanation", "")).strip()
            ai_warnings = result.get("validation_warnings", [])

            if not explanation:
                raise ValueError("empty pricing_explanation in model response")
            if not isinstance(ai_warnings, list) or len(ai_warnings) != len(fallback_warnings):
                # Model didn't follow the warning-count instruction exactly --
                # trust the deterministic fallback for warnings rather than
                # risk a mismatched or invented warning list.
                ai_warnings = fallback_warnings

            # Task 6: sanity-check that Gemini didn't invent a dollar figure
            if not _dollar_figures_valid(explanation, computed):
                return fallback

            return {"pricing_explanation": explanation, "validation_warnings": ai_warnings}
        except Exception as exc:  # noqa: BLE001 - never let an AI hiccup break a pricing run
            is_last = (i == len(models_to_try) - 1)
            if is_last:
                logger.warning(
                    "All models exhausted for %s; using deterministic fallback: %s",
                    computed.get("style_number"), exc,
                )
            else:
                # If auth failed, don't bother trying the fallback model either
                if _ai_disabled_due_to_auth:
                    logger.warning(
                        "Auth error detected; skipping fallback model. "
                        "Using deterministic fallback for %s.",
                        computed.get("style_number"),
                    )
                    break
                logger.warning(
                    "%s failed for %s: %s -- trying fallback model %s",
                    current_model, computed.get("style_number"), exc, models_to_try[i + 1],
                )

    return fallback
