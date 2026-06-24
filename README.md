# Jewelry Pricing Assistant

A deterministic pricing engine for jewelry styles, with AI-generated plain-English explanations layered on top.

Every dollar figure is traceable to an input or a formula. The AI is used solely to *describe* numbers that have already been computed — it never sees raw inputs and is never permitted to invent or override a price.

---

## 🚀 Setup

```bash
git clone https://github.com/bocchi277/jewelry-pricing-assistant.git
cd jewelry-pricing-assistant
python3 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and paste in a free Gemini API key from:
# https://aistudio.google.com/app/apikey
```

The tool runs fully without an API key — it falls back to deterministic template text for explanations and warnings instead of Gemini-generated prose. A clear notice is printed at startup when no key is detected.

> To use a provider other than Gemini, you'll need to make a small change in `ai_explainer._call_gemini()` — see [Swapping Models / Providers](#ai-usage) below.

---

## 📖 Usage
```bash
# Process every row in data/pricing_inputs.csv → outputs/results.json
python3 main.py

# Skip Gemini entirely (CI, offline, or no API key)
python3 main.py --no-ai

# Process a single style
python3 main.py --style B401400-14WVS

# Point at different input files or a custom output path
python3 main.py --pricing data/pricing_inputs.csv --metals data/metal_prices.csv --output outputs/results.json

# Override the primary Gemini model for this run
python3 main.py --model gemini-2.5-flash

# Override the fallback model for this run
python3 main.py --fallback-model gemini-2.0-flash
```

```bash
# Run the test suite
pytest -v
```

---

## Project Structure

```
calculator.py     Deterministic math only — no AI, no I/O, no row-specific logic
validator.py      Input coercion + deterministic warning detection
ai_explainer.py   The only file that talks to Gemini; always has a deterministic fallback
main.py           CLI: loads CSVs, runs everything above, writes JSON
check_gemini.py   Standalone script to verify Gemini API connectivity
tests/            Unit tests for calculator.py / validator.py, plus an end-to-end
                  test against the real provided CSVs
data/             The two provided CSVs (untouched) + an error-handling demo CSV
outputs/          Generated JSON results
```

---

## Pricing Formulas

```
metal_cost             = gold_weight_grams × price_per_gram
diamond_cost           = diamond_carat × diamond_cost_per_carat        (0 if diamond_carat is 0)
color_stone_cost       = color_stone_carat × color_stone_cost_per_carat (0 if color_stone_type is blank)
total_cost             = metal_cost + diamond_cost + color_stone_cost + labor_cost + setting_cost
wholesale_price        = total_cost × (1 + markup_percent / 100)
retail_price           = wholesale_price × 2
```

`14W` / `14Y` / `14R` resolve to the `14K` price; `18W` / `18Y` / `18R` to `18K`; `PT` to `PT`. Mapping is built from `metal_prices.csv`'s `metal_codes` column — nothing is hard-coded. Metal code matching is case-insensitive and whitespace-tolerant (`14w` and ` 14W ` both match `14W`).

All intermediate math runs at full float precision. Rounding to two decimal places happens exactly once, at output time via `calculator.round2`.

### Biggest Cost Driver

The explanation names the single largest cost component among metal, diamond, color stone, labor, and setting. This is computed deterministically in `calculator.biggest_driver()` — ties are broken in that fixed order, so the result is never subject to float noise. A human-readable label is then built in `calculator.driver_label()`:

- `"the natural diamond cost"` vs. `"the lab-grown diamond cost"` — detected from an `LB`-prefixed style number or `"lab grown"` in the item note
- `"the ruby cost"` (or whichever stone is named) for color-stone pieces

The AI receives this label as a given fact. It never decides it.

---

## AI Usage

Gemini (`google-genai`, `gemini-3.1-flash-lite` primary / `gemini-2.5-flash` fallback) is given **only** the already-computed numbers, the already-determined cost-driver label, and the list of already-detected warning facts. Its role is narrowly scoped to two things:

1. Write 1–2 sentences of plain-English `pricing_explanation` — required to name the given driver label, forbidden from stating any dollar amount not provided to it.
2. Rephrase each already-detected warning into one clean sentence for `validation_warnings` — it cannot add, remove, or invent entries. If the count in the response doesn't match the detected count, the output is discarded and the deterministic template is used instead.

A structured `response_schema` (a Pydantic model) forces Gemini to return exactly `{"pricing_explanation": str, "validation_warnings": [str, ...]}` — no prompt-following guesswork.

**Dollar-figure sanity check.** After receiving the AI's response, any `$X.XX` patterns in the explanation are extracted and verified against the computed values (within ±$0.01 tolerance). If Gemini states an amount that doesn't match what it was given, the AI explanation is discarded and the deterministic fallback is used. This is a runtime check, not just a prompt instruction.

**Deterministic fallback.** If no `GEMINI_API_KEY` is set, `--no-ai` is passed, the call raises after retries, or the response fails to parse, `ai_explainer.py` silently falls back to an f-string template for the explanation and to the original warning text. The pipeline never fails because of the AI step.

**Auth-error fast-fail.** If the first Gemini call of a run returns a 401/403/`PERMISSION_DENIED`/invalid-key error, AI is disabled for the rest of the run immediately — rather than retrying three times per row and burning up to 30 failed calls with wall-clock delay. Rate-limit (429) and other transient errors still retry normally.

**Dual-model cascade.** The primary model is tried first. On failure (rate limit, network error, bad response), the fallback model is tried automatically. If both fail, deterministic templates are used. A proactive rate throttler tracks request timestamps per model and sleeps *before* sending a request that would exceed the per-model RPM cap — avoiding 429s rather than reacting to them.

**Swapping models / providers.** Model names are read from `GEMINI_MODEL` and `GEMINI_FALLBACK_MODEL` env vars (or `--model` / `--fallback-model` flags), so changing models is a config change, not a code change. The Gemini-specific code is isolated entirely inside `ai_explainer._call_gemini()`. Swapping to a different provider — OpenAI, Anthropic, a local model — means rewriting that one function to return the same `{"pricing_explanation": ..., "validation_warnings": [...]}` shape. `calculator.py`, `validator.py`, and `main.py` never need to change.

---

## Error Handling

Bad data shouldn't crash a batch job. Invalid inputs are coerced safely and surfaced as `validation_warnings` on the affected row.

| Warning Code | Trigger | Effect |
|---|---|---|
| `MISSING_STYLE_NUMBER` | Row has no style number | Labeled as `(missing style_number)` |
| `DUPLICATE_STYLE_NUMBER` | Same style number on multiple rows (whitespace-insensitive) | Warning attached |
| `MISSING_METAL_CODE` | No metal code provided | Metal cost → $0 |
| `UNKNOWN_METAL_CODE` | Metal code not found in `metal_prices.csv` | Metal cost → $0 |
| `MISSING_GOLD_WEIGHT` | Metal code present but gold weight is blank | Metal cost → $0 |
| `ZERO_GOLD_WEIGHT` | Gold weight is explicitly 0 with a valid metal code | Informational — may be intentional for loose-stone items |
| `MISSING_DIAMOND_QUALITY` | Diamond carat given without a quality grade | Warning only |
| `MISSING_DIAMOND_COST_PER_CARAT` | Diamond carat given without cost per carat | Diamond cost → $0 |
| `MISSING_COLOR_STONE_COST_PER_CARAT` | Color stone carat given without cost per carat | Color stone cost → $0 |
| `COLOR_STONE_CARAT_WITHOUT_TYPE` | Color stone carat given but no stone type | Color stone cost → $0 |
| `COLOR_STONE_TYPE_WITHOUT_CARAT` | Stone type given but carat weight is 0/blank | Color stone cost → $0 |
| `ZERO_COST_PER_CARAT` | Carat > 0 but cost per carat is $0 (likely a forgotten price) | Informational — cost computes to $0 |
| `NON_NUMERIC_VALUE` | Non-numeric value in a numeric column | Treated as 0 |
| `NEGATIVE_VALUE_CLAMPED` | Negative value in a numeric column | Clamped to 0 |
| `MISSING_MARKUP` | `markup_percent` is blank | Treated as 0% |
| `NON_FINITE_RESULT` | A computed price is NaN/Infinity (e.g. from corrupt reference data) | Replaced with $0.00 |

**File-level checks:**

- A missing CSV or a CSV missing a required column exits with a single clear error message instead of a stack trace.
- `metal_prices.csv` is validated on load: every row must have a non-blank `metal_group`, non-blank `metal_codes`, and a finite `price_per_gram > 0`. A malformed row exits with a clear error naming the bad row and column.
- An empty pricing CSV (headers only, no data rows) exits cleanly rather than writing an empty `results.json`.
- An unwritable output path exits with a clear message instead of an unhandled traceback.

To exercise all of these at once:

```bash
python3 main.py --pricing data/error_handling_demo.csv
```

---

## 🔧 Troubleshooting

**"No GEMINI_API_KEY found" warning at startup:**
Copy `.env.example` to `.env` and add your API key, or set
`GEMINI_API_KEY` as an environment variable. The tool works fine without
it — you'll just get deterministic template text instead of Gemini prose.

**Testing Gemini connectivity independently:**
Run `check_gemini.py` to list available models and verify your API key works
without involving the pricing pipeline:

```bash
python3 check_gemini.py
```

**"API key not valid" / auth errors:**
The tool detects auth failures on the first Gemini call and disables AI for
the rest of the run (instead of retrying per-row). Double-check your key at
[Google AI Studio](https://aistudio.google.com/app/apikey).

**Monitoring free-tier usage:**
Free-tier Gemini usage appears in Google AI Studio's **Dashboard → Usage**
page — not in Cloud Billing/cost pages, which may show $0 even when
requests are succeeding.

**Rate-limit slowdowns:**
The tool proactively throttles to stay under per-model RPM caps (e.g.
5 req/min for `gemini-2.5-flash`). If you see "Rate throttle: sleeping…"
messages, this is normal — the tool is pacing itself to avoid 429 errors.

---

## ⚠️ Known Limitations

- **One color stone per item.** The data schema supports a single
  `color_stone_type` / `color_stone_carat` / `color_stone_cost_per_carat`
  per row. Items with multiple color stones would need schema changes.
- **No currency-symbol or percent-sign stripping.** If a numeric field
  contains `"$475"` or `"220%"` instead of `475` or `220`, it will be
  treated as a non-numeric value (clamped to 0 with a warning). Clean your
  CSV inputs to contain plain numbers.
- **Strict `metal_prices.csv` validation.** The reference data file is
  validated on load and will refuse to process if any row has a blank
  `metal_group`, blank `metal_codes`, or a non-positive/non-finite
  `price_per_gram`. This is intentional — corrupt reference data should fail
  loudly, not silently produce wrong prices.

---

## 📊 Sample Outputs

All four samples were generated with `--no-ai`, so `pricing_explanation` and `validation_warnings` come from the deterministic templates. With a real `GEMINI_API_KEY`, every numeric field is bit-for-bit identical — only the wording of those two text fields changes.

**Sample 1 — matches the worked example exactly**
(`python3 main.py --no-ai --style B401400-14WVS`)

```json
{
  "style_number": "B401400-14WVS",
  "metal_group": "14K",
  "metal_cost": 201.6,
  "diamond_cost": 570.0,
  "color_stone_cost": 0.0,
  "labor_cost": 95.0,
  "setting_cost": 40.0,
  "total_cost": 906.6,
  "wholesale_price": 2901.12,
  "retail_price": 5802.24,
  "pricing_explanation": "The largest cost driver is the natural diamond cost. This 14K piece uses a 220% wholesale markup.",
  "validation_warnings": []
}
```

**Sample 2 — lab-grown diamond, labeled correctly**
(`python3 main.py --no-ai --style LB301900-14RVS1`)

```json
{
  "style_number": "LB301900-14RVS1",
  "metal_group": "14K",
  "metal_cost": 193.2,
  "diamond_cost": 262.5,
  "color_stone_cost": 0.0,
  "labor_cost": 100.0,
  "setting_cost": 45.0,
  "total_cost": 600.7,
  "wholesale_price": 1742.03,
  "retail_price": 3484.06,
  "pricing_explanation": "The largest cost driver is the lab-grown diamond cost. This 14K piece uses a 190% wholesale markup.",
  "validation_warnings": []
}
```

**Sample 3 — color-stone-only piece**
(`python3 main.py --no-ai --style B901500-14YS`)

```json
{
  "style_number": "B901500-14YS",
  "metal_group": "14K",
  "metal_cost": 163.8,
  "diamond_cost": 0.0,
  "color_stone_cost": 128.25,
  "labor_cost": 90.0,
  "setting_cost": 35.0,
  "total_cost": 417.05,
  "wholesale_price": 1251.15,
  "retail_price": 2502.3,
  "pricing_explanation": "The largest cost driver is the metal cost (14K). This 14K piece uses a 200% wholesale markup.",
  "validation_warnings": []
}
```

**Sample 4 — error-handling demo, every rule firing at once**
(`python3 main.py --no-ai --pricing data/error_handling_demo.csv`), first two rows of seven:

```json
[
  {
    "style_number": "DEMO-001",
    "metal_group": "14K",
    "metal_cost": 201.6,
    "diamond_cost": 570.0,
    "color_stone_cost": 0.0,
    "labor_cost": 95.0,
    "setting_cost": 40.0,
    "total_cost": 906.6,
    "wholesale_price": 906.6,
    "retail_price": 1813.2,
    "pricing_explanation": "The largest cost driver is the natural diamond cost. This 14K piece uses a 0% wholesale markup.",
    "validation_warnings": [
      "markup_percent was missing or blank; treated as a 0% markup.",
      "A diamond carat weight was provided without a diamond quality grade."
    ]
  },
  {
    "style_number": "DEMO-002",
    "metal_group": "UNKNOWN",
    "metal_cost": 0.0,
    "diamond_cost": 320.0,
    "color_stone_cost": 0.0,
    "labor_cost": 80.0,
    "setting_cost": 30.0,
    "total_cost": 430.0,
    "wholesale_price": 1290.0,
    "retail_price": 2580.0,
    "pricing_explanation": "The largest cost driver is the natural diamond cost. This UNKNOWN piece uses a 200% wholesale markup.",
    "validation_warnings": [
      "Metal code '99X' was not found in metal_prices.csv; metal cost was set to $0.00."
    ]
  }
]
```

Full 7-row output: `outputs/sample_4_error_handling_demo.json`. The remaining rows cover a negative weight + non-numeric labor cost (both clamped with warnings), a color-stone carat without a type, a missing `style_number`, and a duplicate `style_number` across two rows. None of them crash the batch.

Full 10-row run of the provided data: `outputs/results_all_10.json`.

---

## 📝 Notes on AI vs. Deterministic Logic

| Concern | Deterministic | AI |
|---|---|---|
| All 6 cost / price formulas | `calculator.py` | — |
| Metal-code → metal-group mapping | `calculator.build_metal_lookup` | — |
| `diamond_carat == 0` / blank `color_stone_type` rules | `calculator.calculate_costs` | — |
| Which cost bucket is largest | `calculator.biggest_driver` | — |
| Natural vs. lab-grown / named color stone labeling | `calculator.driver_label` | — |
| Whether a warning condition exists | `validator.py` | — |
| Dollar-figure sanity check on AI output | `ai_explainer._dollar_figures_valid` | — |
| Wording of `pricing_explanation` | fallback template | Gemini (when available) |
| Wording of each `validation_warnings` entry | fallback template | Gemini (when available) |