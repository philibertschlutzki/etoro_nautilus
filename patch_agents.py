import re

with open("automation/AGENTS.md", "r") as f:
    content = f.read()

# Make sure we explicitly mention the sibling keys exactly as requested
content = content.replace("- **State/Key Bleed (OOS Gating in `_is_eligible`)**: `oos_metrics` is a sibling key to `metrics` in the backtest result dictionary. Searching for it inside `metrics` (`metrics.get(\"oos_metrics\")`) will silently fail and return `None`, leading to unexpected rejection in tournament gating. Always parse sibling keys directly from the root result dictionary `r`.\n", "- **State/Key Bleed (OOS Gating in `_is_eligible`)**: `oos_metrics` is a sibling key (Geschwister-Key) to `metrics` in the backtest result dictionary. Searching for it inside `metrics` (`metrics.get(\"oos_metrics\")`) will silently fail and return `None`, leading to unexpected rejection in tournament gating. Da `oos_metrics` auf derselben Ebene wie `metrics` liegt und nicht tief verschachtelt ist, muss dieser Fehler bei zukünftigen Aggregations-Modulen von vornherein ausgeschlossen werden. Always parse sibling keys directly from the root result dictionary `r`.\n")

with open("automation/AGENTS.md", "w") as f:
    f.write(content)
