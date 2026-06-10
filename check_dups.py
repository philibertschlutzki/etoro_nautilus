import re, pathlib
from collections import defaultdict

lines = pathlib.Path("automation/AGENTS.md").read_text("utf-8").splitlines()
hits = defaultdict(list)
for i, ln in enumerate(lines, 1):
    s = ln.strip()
    # Erfasst NUR Pitfall-Überschriften/-Bullets (beide Stile); Issue-Überschriften & Fließtext-Verweise werden ignoriert.
    m = re.match(
        r'^(?:#{2,4}\s+🟢?\s*Pitfall\s*#(\d+)'   # ### 🟢 Pitfall #53 —
        r'|#{2,4}\s+🟢?\s*#(\d+)\s*[—:-]'         # ### 🟢 #45 —
        r'|\*\s*\*\*Pitfall\s*#(\d+))', s)        # * **Pitfall #50 ...
    if m:
        n = int(next(g for g in m.groups() if g))
        hits[n].append((i, s[:95]))

dups = {n: v for n, v in sorted(hits.items()) if len(v) > 1}
print("OK: keine doppelten Pitfall-Nummern" if not dups else "KOLLISIONEN:")
for n, v in dups.items():
    print(f"#{n}: {len(v)}×")
    for li, txt in v:
        print(f"   L{li}: {txt}")
