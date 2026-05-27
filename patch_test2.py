with open("tests/test_backtest_runner.py", "r", encoding="utf-8") as f:
    content = f.read()

# Since we don't have quote_tick/test.parquet we bypass it and just pass the file.
content = content.replace('read_precisions_from_parquet(tmp_path / "test.parquet", "test.parquet")', 'read_precisions_from_parquet(tmp_path / "test.parquet")')
content = content.replace('read_precisions_from_parquet(path, "ETH.ETORO")', 'read_precisions_from_parquet(path, "ETH.ETORO")')

with open("tests/test_backtest_runner.py", "w", encoding="utf-8") as f:
    f.write(content)
