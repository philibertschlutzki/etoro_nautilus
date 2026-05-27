with open("automation/backtest_runner.py", "r", encoding="utf-8") as f:
    content = f.read()

import re
match = re.search(r'def read_precisions_from_parquet\(parquet_path: str \| Path, instrument_id: str = None\) -> tuple\[int, int\]:(.*?)\n\n', content, re.DOTALL)
if match:
    print(match.group(0))
