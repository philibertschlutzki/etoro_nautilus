import re

with open("automation/backtest_runner.py", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace("def read_precisions_from_parquet(parquet_path: str | Path, instrument_id: str = None) -> tuple[int, int]:", "def read_precisions_from_parquet(parquet_path: str | Path, instrument_id: str = None) -> tuple[int, int]:")

# Make sure instrument_id_str is not somehow required
content = content.replace("def read_precisions_from_parquet(catalog_path, instrument_id_str)", "def read_precisions_from_parquet(catalog_path, instrument_id_str=None)")
content = content.replace("def read_precisions_from_parquet(catalog_path, iid)", "def read_precisions_from_parquet(catalog_path, iid=None)")

# Look for any def read_precisions_from_parquet and print it out to ensure
import sys
for line in content.splitlines():
    if "def read_precisions_from_parquet" in line:
        print(line)

with open("tests/test_backtest_runner.py", "r", encoding="utf-8") as f:
    test_content = f.read()

test_content = test_content.replace('pp, sp = read_precisions_from_parquet(tmp_path / "test.parquet")', 'pp, sp = read_precisions_from_parquet(tmp_path / "test.parquet", instrument_id="test.parquet")')

with open("tests/test_backtest_runner.py", "w", encoding="utf-8") as f:
    f.write(test_content)
