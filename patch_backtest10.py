with open("automation/backtest_runner.py", "r", encoding="utf-8") as f:
    content = f.read()

import re

replacement = """def read_precisions_from_parquet(parquet_path: str | Path, instrument_id: str = None) -> tuple[int, int]:
    try:
        # Check if parquet_path is a directory and instrument_id is provided
        if instrument_id and (Path(parquet_path) / "data").exists():
            path = Path(parquet_path) / "data" / "quote_tick" / instrument_id
            # Get first parquet file
            parquet_files = list(path.glob("*.parquet"))
            if not parquet_files:
                raise FileNotFoundError()
            target_path = parquet_files[0]
        else:
            target_path = Path(parquet_path)

        schema = pq.read_schema(str(target_path))
        meta = schema.metadata or {}
        if b"price_precision" in meta and b"size_precision" in meta:
            price_prec = int(meta.get(b"price_precision", b"2"))
            size_prec  = int(meta.get(b"size_precision",  b"0"))
            return price_prec, size_prec
        else:
            raise ValueError("Metadata not found")
    except Exception:
        # Fallback via Instrumentname aus Pfad
        symbol = instrument_id if instrument_id else Path(parquet_path).parent.name
        return _fallback_precisions(symbol)
"""

content = re.sub(r'def read_precisions_from_parquet\(parquet_path: str \| Path, instrument_id: str = None\) -> tuple\[int, int\]:(.*?)(?=\n\ndef|\Z)', replacement, content, flags=re.DOTALL)

with open("automation/backtest_runner.py", "w", encoding="utf-8") as f:
    f.write(content)
