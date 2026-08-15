"""Issue #954/#1120 (Katalog #960) — ``check_selection_statistic_economic_bias`` behauptete eine
Ursache, die sie nicht gemessen hat.

Symptom (B-12): Meldung "die Selektion verwirft bevorzugt die profitable Kohorte". Gemessen wurde
ein Median-Return-Unterschied (``oos_total_return``). Der tatsächliche Diskriminator war die
Trade-Zahl: Median 2 gegen 130, 100 % der Kohorte ohne Statistik unter 20 Trades. Beide Mediane
waren negativ — "profitabel" traf auf keine der beiden Kohorten zu.

Root-Cause: ein Return ohne Konditionierung auf Exposure/Trade-Zahl belohnt Nicht-Handeln monoton
(dieselbe Mechanismus-Klasse wie #1077, hier in einer ``high``-Invariante).

Fix (siehe test_issue_965_selection_statistic_economic_bias.py für die vollständige
Regressionsabdeckung der überarbeiteten Funktion):
1. Vergleich auf Expectancy JE TRADE (``oos_expectancy``) statt kumulativem Return umgestellt.
2. Zusätzlich konditioniert auf ``oos_total_trades >= min_trades`` (Default 20); Trials darunter
   bilden eine separat ausgewiesene dritte Kategorie (``n_below_min_trades``).
3. Der Meldungstext nennt ausschliesslich das Gemessene, inklusive der Trade-Zahl beider Kohorten.

Akzeptanzkriterien (verifiziert unten): auf dem B-12-Datensatz besteht der Check nach dem Fix (statt
faelschlich zu FAILen); eine synthetische Null-Trade-Kohorte erzeugt keinen FAIL.
"""
from automation.optimizer import invariants as inv


def _trial(stat_available, expectancy, n_trades):
    return {
        "oos_evaluated": True,
        "oos_selection_statistic_available": stat_available,
        "oos_expectancy": expectancy,
        "oos_total_trades": n_trades,
    }


def test_b12_dataset_no_longer_fails_on_the_trade_count_confound():
    """B-12: median 2 Trades (ohne Statistik) vs. median 130 (mit Statistik), beide Expectancy-
    Mediane negativ. Die alte Fassung (oos_total_return, keine Trade-Zahl-Konditionierung) FAILte
    hier faelschlich mit einer Kausalaussage ueber "Profitabilitaet". Nach dem Fix: entweder PASS
    oder INCONCLUSIVE (n_below_min_trades > 0), aber kein FAIL auf diesem Konfund."""
    trials = (
        [_trial(False, -0.01, 2) for _ in range(30)]
        + [_trial(True, -0.03, 130) for _ in range(100)]
    )
    result = inv.check_selection_statistic_economic_bias(trials)
    assert result.passed is True
    assert result.actual is not None
    assert result.actual["n_below_min_trades"] == 30


def test_synthetic_zero_trade_cohort_never_fails():
    """Akzeptanzkriterium #954 — eine synthetische Null-Trade-Kohorte erzeugt keinen FAIL."""
    trials = (
        [_trial(False, 999.0, 0) for _ in range(25)]
        + [_trial(True, -1.0, 0) for _ in range(25)]
    )
    result = inv.check_selection_statistic_economic_bias(trials)
    assert result.passed is True


def test_real_economic_bias_above_min_trades_still_fires():
    """Der Check bleibt WIRKSAM fuer einen echten Bias oberhalb der Trade-Zahl-Schwelle — der Fix
    entschaerft den Konfund, nicht die Faehigkeit, einen echten Bias zu erkennen."""
    trials = (
        [_trial(False, 0.05, 40) for _ in range(30)]
        + [_trial(True, -0.02, 40) for _ in range(30)]
    )
    result = inv.check_selection_statistic_economic_bias(trials)
    assert result.passed is False
    assert "median_trades=40" in result.detail


def test_message_names_only_what_was_measured():
    trials = (
        [_trial(False, 0.05, 40) for _ in range(30)]
        + [_trial(True, -0.02, 40) for _ in range(30)]
    )
    result = inv.check_selection_statistic_economic_bias(trials)
    assert "Expectancy" in result.detail
    assert "MCAR" in result.detail
