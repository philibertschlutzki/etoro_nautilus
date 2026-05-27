import re

with open("automation/backtest_runner.py", "r", encoding="utf-8") as f:
    content = f.read()

# Fix read_precisions_from_parquet parameters
content = content.replace("def read_precisions_from_parquet(parquet_path, instrument_id: str = None) -> tuple[int, int]:", "def read_precisions_from_parquet(parquet_path: str | Path, instrument_id: str = None) -> tuple[int, int]:")

content = content.replace("def read_precisions_from_parquet(catalog_path, iid)", "def read_precisions_from_parquet(catalog_path, iid)")

with open("automation/backtest_runner.py", "w", encoding="utf-8") as f:
    f.write(content)
