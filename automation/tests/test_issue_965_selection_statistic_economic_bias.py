"""Issue #965 (Katalog A, P0 HEADLINE) — die Selektionsstatistik (``oos_psr``) fehlt im Referenzlauf
``46cf5070`` überproportional GENAU in der profitablen Kohorte: 490 von 2135 OOS-evaluierten Trials
(23,0 %) haben kein definiertes ``oos_psr``, und diese Kohorte hat Median-OOS-Return **+9,50 %**
(92,2 % positiv) gegen **−25,73 %** (92,6 % negativ) für die Kohorte MIT definiertem PSR.

``invariants.check_selection_statistic_availability`` (#915) prüft nur eine ANTEILSSCHWELLE
(>= 80 % verfügbar) — das fängt nicht die Klasse „Statistik fehlt gerade dort, wo sie zählen würde"
(Pitfall #306 in AGENTS.md). ``check_selection_statistic_economic_bias`` schärft das um einen
einseitigen Mann-Whitney-Test: die Kohorte OHNE Statistik darf nicht signifikant PROFITABLER sein
als die Kohorte MIT Statistik.

Issue #954/#1120 (Katalog #960) — B-12 zeigte, dass die urspüngliche Fassung (Vergleich auf
``oos_total_return``, ohne Konditionierung auf Trade-Zahl) einen TRADE-ZAHL-Konfund als
"Selektion verwirft die profitable Kohorte" fehlinterpretierte (Median 2 gegen 130 Trades, beide
Kohorten-Mediane NEGATIV). Der Vergleich läuft seither auf Expectancy JE TRADE
(``oos_expectancy``), zusätzlich konditioniert auf ``oos_total_trades >= min_trades`` — siehe die
Tests ab ``test_b12_reference_scenario_trade_count_confound_no_longer_fires`` unten.
"""
import math

from automation.optimizer import invariants as inv


# ── _mann_whitney_u_one_sided (reine Statistikfunktion, keine scipy-Abhängigkeit) ────────────────
def test_identical_distributions_yield_z_zero_p_half():
    z, p = inv._mann_whitney_u_one_sided([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])
    assert z == 0.0
    assert abs(p - 0.5) < 1e-9


def test_missing_cohort_strictly_greater_gives_low_p():
    z, p = inv._mann_whitney_u_one_sided([10.0, 11.0, 12.0], [1.0, 2.0, 3.0])
    assert z > 0
    assert p < 0.05


def test_missing_cohort_strictly_smaller_gives_high_p_one_sided():
    """H1 ist gerichtet (missing > available) — eine Kohorte, die kleiner ist, verletzt H1 nicht,
    auch wenn die Trennung ebenso extrem ist (das ist der GEWÜNSCHTE, unauffällige Fall)."""
    z, p = inv._mann_whitney_u_one_sided([1.0, 2.0, 3.0], [10.0, 11.0, 12.0])
    assert z < 0
    assert p > 0.95


def test_insufficient_observations_returns_none():
    assert inv._mann_whitney_u_one_sided([1.0], [1.0, 2.0, 3.0]) == (None, None)
    assert inv._mann_whitney_u_one_sided([1.0, 2.0], []) == (None, None)


def test_reference_run_magnitude_is_extremely_significant():
    """Reproduziert die GRÖSSENORDNUNG des Referenzlaufs 46cf5070: 490 Beobachtungen um +9,5 % vs.
    1645 um −25,73 % — der Report behauptet p < 1e-100; unser Normalapproximations-Test soll
    denselben qualitativen Befund (extrem signifikant) liefern."""
    missing = [0.095] * 490
    available = [-0.2573] * 1645
    z, p = inv._mann_whitney_u_one_sided(missing, available)
    assert z > 30
    assert p < 1e-50


# ── check_selection_statistic_economic_bias ───────────────────────────────────────────────────────
# Issue #954/#1120 (Katalog #960) — Root-Cause der ALTEN Fassung (B-12): sie verglich
# oos_total_return (ein Return OHNE Konditionierung auf Trade-Zahl), obwohl der tatsaechliche
# Diskriminator zwischen den beiden Kohorten die TRADE-ZAHL war (Median 2 gegen 130), nicht
# Profitabilitaet (beide Mediane negativ). Fix: Vergleich auf oos_expectancy (je Trade) umgestellt,
# zusaetzlich auf oos_total_trades >= min_trades (Default 20) konditioniert.
def _trial(oos_evaluated, stat_available, oos_expectancy, oos_total_trades=25):
    return {
        "oos_evaluated": oos_evaluated,
        "oos_selection_statistic_available": stat_available,
        "oos_expectancy": oos_expectancy,
        "oos_total_trades": oos_total_trades,
    }


def test_passes_when_missing_cohort_is_not_more_profitable():
    trials = (
        [_trial(True, False, r) for r in (0.01, -0.02, 0.03, -0.01, 0.02)]
        + [_trial(True, True, r) for r in (0.05, 0.04, 0.06, 0.03, 0.07)]
    )
    result = inv.check_selection_statistic_economic_bias(trials)
    assert result.passed is True


def test_fails_when_missing_cohort_reproduces_the_reference_run_bias():
    trials = (
        [_trial(True, False, 0.095) for _ in range(30)]
        + [_trial(True, True, -0.2573) for _ in range(100)]
    )
    result = inv.check_selection_statistic_economic_bias(trials)
    assert result.passed is False
    assert result.severity == "high"
    assert result.actual["n_missing"] == 30
    assert result.actual["n_available"] == 100


def test_ignores_trials_that_were_never_oos_evaluated():
    trials = [
        _trial(False, False, 999.0),  # nicht evaluiert -- muss ignoriert werden
        _trial(True, False, 0.01), _trial(True, False, 0.02),
        _trial(True, True, 0.01), _trial(True, True, 0.02),
    ]
    result = inv.check_selection_statistic_economic_bias(trials)
    assert result.actual is None or result.actual["n_missing"] == 2


def test_insufficient_data_is_a_noop_pass():
    result = inv.check_selection_statistic_economic_bias([_trial(True, False, 0.01)])
    assert result.passed is True
    assert result.actual is None


def test_b12_reference_scenario_trade_count_confound_no_longer_fires():
    """Reproduziert B-12 exakt: beide Kohorten-Mediane sind NEGATIV (nicht "profitabel"), die
    Kohorte OHNE Statistik hat median 2 Trades (< min_trades), die MIT Statistik median 130. Die
    ALTE Fassung (oos_total_return) FAILte hier faelschlich; die NEUE Fassung (Expectancy je Trade,
    min_trades-Filter) darf NICHT auf dem Trade-Zahl-Konfund feuern -- entweder PASS oder
    INCONCLUSIVE, aber kein FAIL, das eine Kausalitaet behauptet, die die Messung nicht traegt."""
    trials = (
        [_trial(True, False, -0.01, oos_total_trades=2) for _ in range(20)]
        + [_trial(True, True, -0.03, oos_total_trades=130) for _ in range(20)]
    )
    result = inv.check_selection_statistic_economic_bias(trials)
    # Nur die 2-Trade-Kohorte (20 Trials) liegt unter min_trades=20 und wird ausgeschlossen; die
    # verbleibende "missing"-Kohorte ist dadurch leer -- kein belastbarer Test moeglich.
    assert result.passed is True
    assert result.actual["n_below_min_trades"] == 20


def test_synthetic_null_trade_cohort_does_not_fail():
    """Akzeptanzkriterium #954 — eine synthetische Kohorte mit durchgehend ~0 Trades erzeugt
    keinen FAIL."""
    trials = (
        [_trial(True, False, 0.5, oos_total_trades=0) for _ in range(15)]
        + [_trial(True, True, -0.1, oos_total_trades=1) for _ in range(15)]
    )
    result = inv.check_selection_statistic_economic_bias(trials)
    assert result.passed is True


def test_trials_below_min_trades_are_excluded_and_counted_separately():
    trials = (
        [_trial(True, False, 0.9, oos_total_trades=3) for _ in range(10)]  # unter min_trades
        + [_trial(True, False, 0.01, oos_total_trades=25) for _ in range(10)]
        + [_trial(True, True, 0.02, oos_total_trades=25) for _ in range(10)]
    )
    result = inv.check_selection_statistic_economic_bias(trials)
    assert result.actual["n_below_min_trades"] == 10
    assert result.actual["n_missing"] == 10
    assert result.actual["n_available"] == 10


def test_message_reports_trade_counts_of_both_cohorts_on_fail():
    trials = (
        [_trial(True, False, 0.095, oos_total_trades=25) for _ in range(30)]
        + [_trial(True, True, -0.2573, oos_total_trades=130) for _ in range(100)]
    )
    result = inv.check_selection_statistic_economic_bias(trials)
    assert result.passed is False
    assert result.actual["median_n_trades_missing"] == 25
    assert result.actual["median_n_trades_available"] == 130
    assert "median_trades=25" in result.detail
    assert "median_trades=130" in result.detail


def test_custom_min_trades_threshold_is_respected():
    trials = (
        [_trial(True, False, 0.9, oos_total_trades=8) for _ in range(10)]
        + [_trial(True, True, 0.01, oos_total_trades=8) for _ in range(10)]
    )
    # min_trades=20 (Default) schliesst beide Kohorten aus.
    assert inv.check_selection_statistic_economic_bias(trials).actual["n_below_min_trades"] == 20
    # min_trades=5 laesst beide Kohorten durch den Test.
    result = inv.check_selection_statistic_economic_bias(trials, min_trades=5)
    assert result.actual["n_missing"] == 10
    assert result.actual["n_available"] == 10
