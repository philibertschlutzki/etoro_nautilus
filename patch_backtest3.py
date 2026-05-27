import re

with open("automation/backtest_runner.py", "r", encoding="utf-8") as f:
    content = f.read()

new_imports = """
import pyarrow.parquet as pq
from automation.utils import _fallback_precisions
import importlib
from dotenv import load_dotenv

_AUTOMATION_DIR = Path(__file__).resolve().parent.parent
if str(_AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(_AUTOMATION_DIR))

# Search .env in automation/ first, then in PROJECT_ROOT (fallback)
_THIS_DIR = Path(__file__).resolve().parent
ENV_FILE = _THIS_DIR / ".env"
if not ENV_FILE.exists():
    ENV_FILE = _THIS_DIR.parent / ".env"
load_dotenv(str(ENV_FILE))

def read_precisions_from_parquet(parquet_path, instrument_id: str = None) -> tuple[int, int]:
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

def score_strategy(metrics: dict, scoring_cfg: dict) -> float:
    score = 0.0
    score += metrics.get("sortino_ratio", 0) * scoring_cfg.get("sortino_weight", 0.4)
    score += metrics.get("profit_factor", 0) * scoring_cfg.get("profit_factor_weight", 0.3)
    score += metrics.get("win_rate", 0) * scoring_cfg.get("win_rate_weight", 0.2)
    score -= metrics.get("max_drawdown", 0) * scoring_cfg.get("drawdown_penalty_weight", 0.1)
    return score

def select_tournament_winner(results: list[dict], cfg: dict) -> dict | None:
    best_candidate = None
    best_score = -999.0
    for r in results:
        metrics = r.get("metrics", {})
        if not _is_eligible(metrics, cfg):
            continue
        score = score_strategy(metrics, cfg.get("scoring", {}))
        if score > best_score:
            best_score = score
            best_candidate = r
    if best_candidate:
        return best_candidate
    return None
"""

content = content.replace("from pathlib import Path", "from pathlib import Path\n" + new_imports)

# Replace the read_precisions_from_parquet calls
content = re.sub(r'from adapters\..+? import .+\n', '', content)
content = content.replace("from automation.config.strategies import", "# ")

# Use new method in create_mock_instrument
content = re.sub(r'_get_size_precision\([^)]+\)', '8', content)  # Hacky replace for any internal size calls

with open("automation/backtest_runner.py", "w", encoding="utf-8") as f:
    f.write(content)
