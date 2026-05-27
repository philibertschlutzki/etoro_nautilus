import re

with open("automation/backtest_runner.py", "r", encoding="utf-8") as f:
    content = f.read()

# Add CLI flags if missing
# --single-symbol, --strategy are required
cli_additions = """
    parser.add_argument("--single-symbol", type=str, help="Nur ein Symbol backtesten (z.B. AERO.ETORO)")
    parser.add_argument("--strategy", type=str, help="Nur eine Strategie testen (z.B. VwapExhaustionStrategy)")
"""
if "--single-symbol" not in content:
    content = content.replace('parser.add_argument("--htmlreport"', cli_additions + '\n    parser.add_argument("--htmlreport"')

with open("automation/backtest_runner.py", "w", encoding="utf-8") as f:
    f.write(content)
