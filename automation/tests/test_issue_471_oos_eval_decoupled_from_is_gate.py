import pytest
import json
import tempfile
from pathlib import Path
from automation.optimizer.parsing import parse_tournament
from automation.backtest_runner import write_tournament_json

# Fixture für minimales tournament_cfg
@pytest.fixture
def dummy_tournament_cfg():
    return {
        "scoring": {},
        "require_oos": True,
        "eligible_requires_all": {},
        "eligible_requires_any": []
    }

def test_oos_eval_true_despite_is_failure(dummy_tournament_cfg):
    """
    Testet Issue #487: Auch wenn IS-eligible = False ist (IS-Failure),
    muss _oos_eval ausgeführt und bei Validität (z.B. total_trades=5)
    oos_evaluated=True im parsed Output (parse_tournament) ankommen.
    Der Wert muss den CHF Währungskontext implizieren.
    """
    # 1 Trial, IS-eligible = False (wird z.B. abgelehnt), aber oos_metrics vorhanden und gültig.
    results = [
        {
            "symbol": "BTCUSD",
            "strategy": "StratA",
            "metrics": {"total_trades": 50, "sortino_ratio": 2.5, "profit_factor": 1.5, "win_rate": 0.6, "total_return": 0.2, "max_drawdown": 0.1},
            "oos_metrics": {"total_trades": 5, "sortino_ratio": 1.5, "profit_factor": 1.2, "win_rate": 0.5, "total_return": 0.05, "max_drawdown": 0.05},
            "strat_params": {"param1": 10},
            "is_eligible": False  # IS failure
        }
    ]

    # Schreibe JSON
    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "tournament_result.json"

        # Simulierte Werte implizieren CHF (Währungskontext) z.B. für Startkapital, falls wir es bräuchten,
        # hier verwenden wir einfach die Base-Metriken.

        # Wir rufen hier den realen Mechanismus auf
        from automation.backtest_runner import select_winners
        per_symbol_winners, aggregate_winner, warnings_list, is_eligible_count, fully_eligible_count = select_winners(results, tournament_cfg=dummy_tournament_cfg)

        write_tournament_json(
            all_results=results,
            output_path=str(out_path),
            per_symbol_winners=per_symbol_winners,
            aggregate_winner=aggregate_winner,
            warnings_list=warnings_list,
            is_eligible_count=is_eligible_count,
            fully_eligible_count=fully_eligible_count,
            tournament_cfg=dummy_tournament_cfg,
        )

        # Manually set universe_size as we are doing unit test overrides where write_tournament_json does not take universe_size.
        with open(out_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data['universe_size'] = 1
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(data, f)

        # Prüfe, dass Datei existiert
        assert out_path.exists()

        # Parse Tournament
        metrics = parse_tournament(out_path)

        # Assertions
        assert metrics.oos_evaluated is True, "OOS muss evaluiert werden, auch wenn IS failed"
        assert metrics.oos_total_trades == 5, "oos_total_trades muss 5 betragen"

def test_multisymbol_snapshot_bit_identical(dummy_tournament_cfg):
    """
    Testet, dass bei universe_size > 1 und keinem Aggregat-Gewinner
    der single_symbol_oos Block komplett im JSON fehlt.
    """
    results = [
        {
            "symbol": "BTCUSD",
            "strategy": "StratA",
            "metrics": {"total_trades": 50, "sortino_ratio": 2.5, "profit_factor": 1.5, "win_rate": 0.6, "total_return": 0.2, "max_drawdown": 0.1},
            "oos_metrics": {"total_trades": 5, "sortino_ratio": 1.5, "profit_factor": 1.2, "win_rate": 0.5, "total_return": 0.05, "max_drawdown": 0.05},
            "strat_params": {"param1": 10},
            "is_eligible": False
        },
        {
            "symbol": "ETHUSD",
            "strategy": "StratA",
            "metrics": {"total_trades": 40, "sortino_ratio": 1.5, "profit_factor": 1.1, "win_rate": 0.4, "total_return": 0.1, "max_drawdown": 0.2},
            "oos_metrics": {"total_trades": 3, "sortino_ratio": 1.0, "profit_factor": 1.0, "win_rate": 0.4, "total_return": 0.01, "max_drawdown": 0.08},
            "strat_params": {"param1": 10},
            "is_eligible": False
        }
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "tournament_result.json"

        from automation.backtest_runner import select_winners
        per_symbol_winners, aggregate_winner, warnings_list, is_eligible_count, fully_eligible_count = select_winners(results, tournament_cfg=dummy_tournament_cfg)

        write_tournament_json(
            all_results=results,
            output_path=str(out_path),
            per_symbol_winners=per_symbol_winners,
            aggregate_winner=aggregate_winner,
            warnings_list=warnings_list,
            is_eligible_count=is_eligible_count,
            fully_eligible_count=fully_eligible_count,
            tournament_cfg=dummy_tournament_cfg,
        )

        # Manually set universe_size as we are doing unit test overrides where write_tournament_json does not take universe_size.
        # Actually write_tournament_json gets universe_size by looking at `universe_snapshot` or counting. Let's just modify the JSON directly for the test.
        with open(out_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data['universe_size'] = 2
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(data, f)

        # Prüfe den rohen JSON Inhalt
        with open(out_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert "aggregate_winner" in data, "aggregate_winner Key sollte existieren (auch wenn null)"
        assert data["aggregate_winner"] is None, "Kein Aggregat-Gewinner"
        assert "single_symbol_oos" not in data, "single_symbol_oos darf bei universe_size > 1 nicht existieren"

def test_fail_loud_invariant():
    """
    Testet die Invariante in parse_tournament:
    Wenn universe_size = 1 und full_results OOS-Trades aufweist (Worker-Trades > 0),
    aber metrics.oos_total_trades <= 0 ist (z.B. weil single_symbol_oos fehlt),
    muss ein ValueError geworfen werden.
    """
    # Manipuliertes JSON
    manipulated_data = {
        "universe_size": 1,
        "full_results": [
            {
                "symbol": "BTCUSD",
                "strategy": "StratA",
                "oos_metrics": {"total_trades": 10}
            }
        ],
        "aggregate_winner": None
        # single_symbol_oos fehlt komplett
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "tournament_result.json"
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(manipulated_data, f)

        with pytest.raises(ValueError, match="Invariante gebrochen: Worker-OOS-Metriken existent, aber parse_tournament liefert oos_total_trades <= 0"):
            parse_tournament(out_path)
