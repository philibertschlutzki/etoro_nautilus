with open("automation/backtest_runner.py", "r", encoding="utf-8") as f:
    content = f.read()

# I see we have two read_precisions_from_parquet, let's remove the second one.
lines = content.splitlines()
new_lines = []
skip = False
for line in lines:
    if line.startswith("def read_precisions_from_parquet(catalog_path: str, instrument_id_str: str) -> tuple[int, int]:"):
        skip = True
        continue
    if skip:
        if line.startswith("def ") or line.startswith("class ") or line.startswith("import ") or line.startswith("_") and not line.startswith("    "):
            skip = False
        else:
            continue
    new_lines.append(line)

with open("automation/backtest_runner.py", "w", encoding="utf-8") as f:
    f.write("\n".join(new_lines) + "\n")
