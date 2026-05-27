with open("automation/backtest_runner.py", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace('schema.metadata or {}', 'schema.metadata or {}')

# Update read_precisions_from_parquet so it uses correct metadata logic
replacement = """def read_precisions_from_parquet(parquet_path: str | Path, instrument_id: str = None) -> tuple[int, int]:
    try:
        if instrument_id:
            path = Path(parquet_path) / "data" / "quote_tick" / instrument_id
            parquet_files = list(path.glob("*.parquet"))
            if not parquet_files:
                raise FileNotFoundError()
            target_path = parquet_files[0]
        else:
            target_path = Path(parquet_path)

        schema = pq.read_schema(str(target_path))
        meta = schema.metadata or {}
        price_prec = int(meta.get(b"price_precision", b"2"))
        size_prec  = int(meta.get(b"size_precision",  b"0"))
        return price_prec, size_prec
    except Exception:
        symbol = instrument_id if instrument_id else Path(parquet_path).parent.name
        return _fallback_precisions(symbol)
"""

import re
content = re.sub(r'def read_precisions_from_parquet\(parquet_path.*?return _fallback_precisions\(symbol\)', replacement, content, flags=re.DOTALL)

with open("automation/backtest_runner.py", "w", encoding="utf-8") as f:
    f.write(content)
