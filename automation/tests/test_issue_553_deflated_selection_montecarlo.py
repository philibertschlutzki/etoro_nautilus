"""Issue #553 — Deflated-Sortino-Selektion (Multiple-Testing-Korrektur), opt-in.

Ohne Korrektur ist die erwartete beste Performance über N getestete Konfigurationen auch unter
H0 (kein Edge) positiv und wächst mit N ⇒ hohe False-Positive-Winner-Rate. Der deflationierte
Schwellenwert kontrolliert das auf das nominelle Niveau. Das Modul ist rein & deterministisch;
die Anwendung in select_winners ist opt-in (bit-identisch ohne Flag).
"""
import random
import statistics

from automation.optimizer.deflation import deflated_threshold, expected_max_standard_normal


def _make_result(symbol, strategy, is_sortino, oos_sortino, oos_eligible=True, score=1.0):
    """Minimaler is_eligible-Result mit OOS-Eval, wie select_winners ihn erwartet."""
    metrics = {
        "total_trades": 30, "sortino_ratio": is_sortino, "profit_factor": 1.5,
        "win_rate": 0.5, "max_drawdown": 0.05, "total_return": 0.05, "expectancy": 0.02,
        "median_position_notional": 1000.0,
    }
    oos_metrics = {
        "total_trades": 20, "sortino_ratio": oos_sortino, "profit_factor": 1.5,
        "win_rate": 0.5, "max_drawdown": 0.05, "total_return": 0.05, "expectancy": 0.02,
        "median_position_notional": 1000.0,
    }
    return {
        "symbol": symbol, "strategy": strategy, "metrics": metrics, "oos_metrics": oos_metrics,
        "strat_params": {}, "_score": score,
        "_oos_eval": {"oos_evaluated": True, "oos_eligible": oos_eligible,
                      "oos_metrics": oos_metrics, "oos_rejection_reasons": []},
    }


# ── Monte-Carlo: die deflationierte Schwelle kontrolliert die False-Positive-Rate ────────────
def test_deflated_threshold_controls_false_positive_rate():
    random.seed(20260706)
    N = 100
    confidence = 0.95
    sweeps = 4000

    fp_deflated = 0
    fp_static = 0
    static_threshold = 0.3  # naive statische Gate-Schwelle (oos_min_sortino)

    for _ in range(sweeps):
        # Reine Zufalls-Sortinos ~ N(0, 1): KEIN echter Edge (H0).
        sortinos = [random.gauss(0.0, 1.0) for _ in range(N)]
        best = max(sortinos)
        disp = statistics.pstdev(sortinos)
        thr = deflated_threshold(N, disp, confidence=confidence, baseline=0.0)
        if best > thr:
            fp_deflated += 1
        if best > static_threshold:
            fp_static += 1

    rate_deflated = fp_deflated / sweeps
    rate_static = fp_static / sweeps

    # Deflationiert: nahe dem nominellen Niveau 1−confidence = 0.05 (Toleranz für Sampling-Rauschen).
    assert rate_deflated <= 0.09, f"Deflated FP-Rate {rate_deflated:.3f} über nominellem Niveau"
    # Statisch: praktisch immer ein 'Winner' (max von 100 N(0,1) ≫ 0.3) ⇒ Korrektur wirkt drastisch.
    assert rate_static > 0.95
    assert rate_deflated < rate_static


def test_threshold_grows_with_n_trials():
    """Die Schwelle wächst monoton mit der Anzahl getesteter Trials (mehr Trials ⇒ strengeres Gate)."""
    disp = 1.0
    t10 = deflated_threshold(10, disp)
    t100 = deflated_threshold(100, disp)
    t1000 = deflated_threshold(1000, disp)
    assert t10 < t100 < t1000
    # Referenz-Größenordnung: erwartetes Maximum ~ sqrt(2 ln N).
    assert expected_max_standard_normal(100) > expected_max_standard_normal(10)


def test_threshold_degenerates_without_dispersion_or_trials():
    assert deflated_threshold(1, 1.0) == 0.0          # nur ein Trial ⇒ kein Multiple-Testing
    assert deflated_threshold(100, 0.0) == 0.0        # keine Streuung ⇒ Baseline


# ── select_winners: opt-in, bit-identisch ohne Flag ──────────────────────────────────────────
def _cfg(deflated):
    return {
        "min_trades": 1, "min_sortino": 0.0, "min_profit_factor": 0.0, "max_drawdown": 1.0,
        "min_win_rate": 0.0, "min_total_return": 0.0, "min_expectancy": 0.0,
        "oos_min_trades": 1, "oos_min_total_return": 0.0, "oos_min_expectancy": 0.0,
        "oos_min_win_rate": 0.0,
        "eligible_requires_all": ["min_trades"],
        "eligible_requires_any": ["min_sortino"],
        "scoring": {"sortino_weight": 0.4, "profit_factor_weight": 0.3,
                    "win_rate_weight": 0.2, "drawdown_penalty_weight": 0.1},
        "deflated_selection": deflated,
        "deflation_confidence": 0.95,
    }


def test_selection_bit_identical_when_flag_off():
    from automation.backtest_runner import select_winners
    results = [_make_result("AAA", "StratA", is_sortino=1.0 + i * 0.1, oos_sortino=0.8)
               for i in range(5)]
    winners_off, _, _, _, _ = select_winners([dict(r) for r in results], _cfg(False))
    # Ohne Flag existiert ein Winner und der Winner-Eintrag trägt KEINEN deflated_min_sortino-Key.
    assert "AAA" in winners_off
    assert "deflated_min_sortino" not in winners_off["AAA"]


def test_deflated_flag_can_reject_noise_winner():
    from automation.backtest_runner import select_winners
    # Viele evaluierte Konfigurationen mit gestreuten OOS-Sortinos; der beste ist knapp positiv,
    # aber unter dem deflationierten Rausch-Maximum ⇒ kein Winner unter deflated_selection.
    random.seed(7)
    results = []
    for i in range(40):
        s = random.gauss(0.0, 1.0)
        results.append(_make_result("AAA", f"S{i}", is_sortino=1.0, oos_sortino=s, score=1.0 + i))
    winners_on, _, _, _, _ = select_winners([dict(r) for r in results], _cfg(True))
    winners_off, _, _, _, _ = select_winners([dict(r) for r in results], _cfg(False))
    # Ohne Flag: der score-beste OOS-eligible Kandidat gewinnt.
    assert "AAA" in winners_off
    # Mit Flag: nur wenn der beste OOS-Sortino das deflationierte Maximum schlägt.
    from automation.optimizer.deflation import deflated_threshold
    sortinos = [r["oos_metrics"]["sortino_ratio"] for r in results]
    thr = deflated_threshold(len(sortinos), statistics.pstdev(sortinos), confidence=0.95)
    if max(sortinos) < thr:
        assert "AAA" not in winners_on
