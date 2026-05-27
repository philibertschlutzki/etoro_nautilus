import json
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

_ENV = Path("automation/.env")
if not _ENV.exists():
    _ENV = Path(".env")
load_dotenv(str(_ENV))

class TestBacktestRunner:

    def test_read_precisions_from_parquet_with_meta(self, tmp_path):
        """Precisions korrekt aus Parquet-Metadaten lesen."""
        from automation.backtest_runner import read_precisions_from_parquet
        import pyarrow as pa
        import pyarrow.parquet as pq

        meta = {
            b"price_precision": b"2",
            b"size_precision": b"8"
        }
        schema = pa.schema([('test', pa.string())], metadata=meta)
        table = pa.Table.from_arrays([pa.array(['a'])], schema=schema)
        pq.write_table(table, str(tmp_path / "test.parquet"))

        pp, sp = read_precisions_from_parquet(tmp_path / "test.parquet", instrument_id="test.parquet")
        assert pp == 2
        assert sp == 8

    def test_read_precisions_fallback(self, tmp_path):
        """Fallback wenn keine Metadaten vorhanden."""
        from automation.backtest_runner import read_precisions_from_parquet
        import pyarrow as pa
        import pyarrow.parquet as pq

        schema = pa.schema([('test', pa.string())])
        table = pa.Table.from_arrays([pa.array(['a'])], schema=schema)
        path = tmp_path / "ETH.ETORO" / "test.parquet"
        path.parent.mkdir()
        pq.write_table(table, str(path))

        pp, sp = read_precisions_from_parquet(path, "ETH.ETORO")
        assert sp == 8  # Crypto-Fallback

    def test_score_strategy_weights(self):
        """Score berechnet sich korrekt aus tournament.json Gewichten."""
        from automation.backtest_runner import score_strategy
        cfg = json.loads(Path("automation/config/tournament.json").read_text())
        metrics = {
            "sortino_ratio": 2.0,
            "profit_factor": 1.5,
            "win_rate": 0.6,
            "max_drawdown": 0.1,
        }
        score = score_strategy(metrics, cfg["scoring"])
        assert isinstance(score, float)
        assert score > 0

    def test_select_tournament_winner_filters_low_trades(self):
        """min_trades=10: Kandidat mit 8 Trades wird gefiltert."""
        from automation.backtest_runner import select_tournament_winner
        cfg = json.loads(Path("automation/config/tournament.json").read_text())
        results = [
            {"total_trades": 8, "profit_factor": 999.0,
             "sortino_ratio": 0.0, "win_rate": 1.0,
             "max_drawdown": 0.0, "total_return": 0.1},
        ]

        # Override to ensure eligibility handles metrics dict inside r
        results_formatted = [{"metrics": r} for r in results]

        # Hack because we can't easily import _is_eligible from backtest_runner directly in isolation test without running into errors
        # but select_tournament_winner internally calls _is_eligible, so it should filter it.
        winner = select_tournament_winner(results_formatted, cfg)
        assert winner is None

    def test_cli_help(self):
        """CLI --help gibt alle 6 Flags aus."""
        result = subprocess.run(
            [sys.executable, "automation/backtest_runner.py", "--help"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        for flag in ["--momentum", "--catalog-path", "--config",
                     "--output", "--single-symbol", "--strategy"]:
            assert flag in result.stdout, f"Flag {flag} fehlt in --help"
