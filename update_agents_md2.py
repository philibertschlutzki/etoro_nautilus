import re

with open("automation/AGENTS.md", "r", encoding="utf-8") as f:
    content = f.read()

# Update Pitfall 88
content = content.replace("OOS-Start = IS-Start + IS-Window + Embargo-Period.",
                          "OOS-Start = IS-Start + IS-Window + Embargo-Period. Diese mengentheoretische Invariante (Lookback_Window ∩ IS_Period = ∅) schreibt den Embargo-Mechanismus vor.")

# Update Pitfall 89
content = content.replace("Jede Code-Änderung der Penalty-/Distanz-Logik erfordert den Bump der `reward_semantics_version` in `optimizer.json`.",
                          "Jede Modifikation an der mathematischen Distanz-Kalkulation, Penalty-Gewichtung oder Gate-Logik erfordert einen zwingenden Bump der `reward_semantics_version` in der `optimizer.json`.")

with open("automation/AGENTS.md", "w", encoding="utf-8") as f:
    f.write(content)
