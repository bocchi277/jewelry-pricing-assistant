import json

with open('Jewelry_Pricing_Assistant_Demo.ipynb', 'r') as f:
    notebook = json.load(f)

replacements = {
    # Markdown
    "# 💎 ": "# ",
    "### 📋 ": "### ",
    "## 🔧 1.": "## 1.",
    "### 📂 ": "### ",
    "### 📊 ": "### ",
    "## 💍 2.": "## 2.",
    "## 📦 3.": "## 3.",
    "### 🔬 ": "### ",
    "## ⚠️ 4.": "## 4.",
    "## 🤖 5.": "## 5.",
    "## 🧪 6.": "## 6.",
    "## 🏗️ 7.": "## 7.",
    "### 🔧 ": "### ",
    "## ✅ Summary": "## Summary",
    
    # Table Content
    "| ❌ Never |": "| No |",
    "| ✅ Optional |": "| Yes (Optional) |",
    "| ✅ Verified against spec |": "| Verified against spec |",
    "| ✅ No crashes |": "| No crashes |",
    "| ✅ Correct |": "| Correct |",
    "| ✅ Both costs computed |": "| Both costs computed |",
    "| ✅ Clear warnings, no crashes |": "| Clear warnings, no crashes |",
    "| ✅ Never changes numbers |": "| Never changes numbers |",
    "| ✅ Tested |": "| Tested |",
    "| ✅ All passing |": "| All passing |",

    # Code Prints
    "✅ Setup complete!": "Setup complete!",
    "📁 Project Structure:": "Project Structure:",
    "  📂 ": "  [DIR] ",
    "  📄 ": "  [FILE] ",
    "📋 metal_prices.csv": "metal_prices.csv",
    "📋 pricing_inputs.csv": "pricing_inputs.csv",
    "📊 RESULT for": "RESULT for",
    "✅ VERIFICATION": "VERIFICATION",
    "\"✅\" if actual == expected else \"❌\"": "\"[PASS]\" if actual == expected else \"[FAIL]\"",
    "🎉 All values match": "SUCCESS: All values match",
    "⚠️  Some values differ!": "WARNING: Some values differ!",
    "📝 Explanation: ": "Explanation: ",
    "⚠️  Warnings: ": "Warnings: ",
    "📊 Processed ": "Processed ",
    "🔬 Diamond Type Detection:": "Diamond Type Detection:",
    "'🧪 Lab-grown' if is_lab else '💎 Natural' if is_natural else '❓ Unknown'": "'Lab-grown' if is_lab else 'Natural' if is_natural else 'Unknown'",
    "📋 Error-handling demo input:": "Error-handling demo input:",
    "✅ All {len(error_results)} rows": "SUCCESS: All {len(error_results)} rows",
    "🏷️  {r['style_number']}": "Style: {r['style_number']}",
    "   ⚠️  Warnings": "   Warnings",
    "   ✅ No warnings": "   No warnings",
    "🔑 API key set!": "API key set!",
    "🔄 COMPARISON:": "COMPARISON:",
    "🤖 AI-generated explanation:": "AI-generated explanation:",
    "📐 Deterministic fallback:": "Deterministic fallback:",
    "💰 Prices are IDENTICAL": "Prices are IDENTICAL",
    "\"✅\" if ai_val == det_val else \"❌\"": "\"[MATCH]\" if ai_val == det_val else \"[MISMATCH]\"",
    "⏭️  No API key provided": "SKIPPED: No API key provided",
    "🛠️  Custom Pricing Calculation:": "Custom Pricing Calculation:",
    "📌 Biggest cost driver:": "Biggest cost driver:",
    
    # Catch any remaining single emojis (some might not match exact strings above)
    "✅": "PASS",
    "❌": "FAIL",
    "⚠️": "WARNING",
    "🎉": "SUCCESS",
    "💎": "Diamond",
    "📋": "List",
    "🔧": "Tool",
    "📂": "Dir",
    "📄": "File",
    "📊": "Stats",
    "💍": "Ring",
    "📦": "Box",
    "🔬": "Lab",
    "🧪": "Test",
    "❓": "?",
    "🏷️": "Tag",
    "🤖": "AI",
    "🔄": "Sync",
    "📐": "Rule",
    "💰": "Price",
    "⏭️": "Skip",
    "🏗️": "Build",
    "🛠️": "Config",
    "📌": "Note"
}

def clean_source(source_lines):
    new_lines = []
    for line in source_lines:
        new_line = line
        for old, new in replacements.items():
            new_line = new_line.replace(old, new)
        new_lines.append(new_line)
    return new_lines

for cell in notebook.get('cells', []):
    if 'source' in cell:
        cell['source'] = clean_source(cell['source'])

with open('Jewelry_Pricing_Assistant_Demo.ipynb', 'w') as f:
    json.dump(notebook, f, indent=1)
    f.write('\n')  # Add trailing newline for Colab format

print("Notebook cleaned successfully.")
