"""Issue #567 — Deflated-Sortino-Selektion aktiviert (Multiple-Testing-Kontrolle).

Bei 100–200 Konfigurationen je Study wird das MAXIMUM vieler verrauschter Sortinos selektiert;
das erwartete Rausch-Maximum wächst mit √(2 ln N) und überfittet (Bailey/López de Prado). Die
Implementierung (deflation.py) existierte, war aber deaktiviert. Issue #567 schaltet sie scharf.

Akzeptanzkriterien (Issue #567):
- deflated_min_sortino erscheint im Winner-Eintrag (und wird je Study/Symbol geloggt).
- Auf einer synthetischen Null-Alpha-Rausch-Study werden ≤ 5 % der Konfigurationen als Winner
  deklariert (nominelle FP-Rate 1 − confidence).
- Produktions-Config hat deflated_selection=true, deflation_confidence=0.95.
"""
import json
import random
import statistics
from pathlib import Path

from automation.backtest_runner import select_winners
from automation.optimizer.deflation import deflated_threshold

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def _result(symbol, strategy, oos_sortino, score=1.0):
    metrics = {
        "total_trades": 30, "sortino_ratio": 2.0, "profit_factor": 1.5, "win_rate": 0.5,
        "max_drawdown": 0.05, "total_return": 0.05, "expectancy": 0.02,
        "median_position_notional": 1000.0,
    }
    oos = {
        "total_trades": 20, "sortino_ratio": oos_sortino, "profit_factor": 1.5, "win_rate": 0.5,
        "max_drawdown": 0.05, "total_return": 0.05, "expectancy": 0.02,
        "median_position_notional": 1000.0,
    }
    return {
        "symbol": symbol, "strategy": strategy, "metrics": metrics, "oos_metrics": oos,
        "strat_params": {}, "_score": score,
        "_oos_eval": {"oos_evaluated": True, "oos_eligible": True,
                      "oos_metrics": oos, "oos_rejection_reasons": []},
    }


def _cfg(deflated):
    return {
        "min_trades": 1, "min_sortino": 0.0, "min_profit_factor": 0.0, "max_drawdown": 1.0,
        "min_win_rate": 0.0, "min_total_return": 0.0, "min_expectancy": 0.0,
        "oos_min_trades": 1, "oos_min_total_return": 0.0, "oos_min_expectancy": 0.0,
        "oos_min_win_rate": 0.0, "oos_min_sortino": 0.0, "oos_min_profit_factor": 0.0,
        "eligible_requires_all": ["min_trades"], "eligible_requires_any": ["min_sortino"],
        "scoring": {"sortino_weight": 0.4, "profit_factor_weight": 0.3,
                    "win_rate_weight": 0.2, "drawdown_penalty_weight": 0.1},
        "deflated_selection": deflated, "deflation_confidence": 0.95,
    }


# ── Produktions-Config scharfgeschaltet ──────────────────────────────────────────────────────
def test_production_config_enables_deflated_selection():
    assert TCFG["deflated_selection"] is True
    assert TCFG["deflation_confidence"] == 0.95


# ── Winner-Eintrag trägt deflated_min_sortino ────────────────────────────────────────────────
def test_winner_entry_carries_deflated_threshold():
    random.seed(11)
    # Ein klarer Gewinner (Sortino weit über dem Rausch-Maximum) + Rausch-Kandidaten.
    results = [_result("AAA", f"S{i}", oos_sortino=random.gauss(0.0, 1.0), score=1.0 + i)
               for i in range(30)]
    results.append(_result("AAA", "WINNER", oos_sortino=8.0, score=100.0))
    winners, _, _, _, _ = select_winners([dict(r) for r in results], _cfg(True))
    assert "AAA" in winners
    assert "deflated_min_sortino" in winners["AAA"], "Winner-Eintrag muss deflated_min_sortino tragen."
    assert winners["AAA"]["deflated_min_sortino"] > 0.0


# ── Null-Alpha-Rausch-Study: ≤ 5 % Winner-Rate ───────────────────────────────────────────────
def test_null_alpha_false_positive_rate_controlled():
    random.seed(20260706)
    N = 100          # Konfigurationen je Study
    M = 400          # unabhängige Studien (Symbole)
    winner_studies = 0
    for s in range(M):
        results = [_result(f"SYM{s}", f"S{i}", oos_sortino=random.gauss(0.0, 1.0), score=1.0 + i)
                   for i in range(N)]
        winners, _, _, _, _ = select_winners([dict(r) for r in results], _cfg(True))
        if winners:
            winner_studies += 1
    fp_rate = winner_studies / M
    # Nominelles Niveau 1 − confidence = 5 %; Toleranz für Sampling-Rauschen.
    assert fp_rate <= 0.09, f"FP-Winner-Rate {fp_rate:.3f} über nominellem Niveau (5 %)."


def test_deflation_off_declares_noise_winner():
    """Ohne Deflation gewinnt auf derselben Rausch-Study praktisch immer ein Kandidat (Kontrast)."""
    random.seed(3)
    results = [_result("BBB", f"S{i}", oos_sortino=random.gauss(0.0, 1.0), score=1.0 + i)
               for i in range(100)]
    winners_off, _, _, _, _ = select_winners([dict(r) for r in results], _cfg(False))
    assert "BBB" in winners_off  # das Rausch-Maximum passiert das statische (Null-)Gate
    assert "deflated_min_sortino" not in winners_off["BBB"]
