"""Issue #1262 (GH #1132) — ``check_effective_stop_distance`` muss den Gewinner beurteilen, nicht
den Kohorten-Median.

Symptom. In 3 von 13 Studies liegt der Gewinner auf der anderen Seite der Schwelle 10,0 als der
Kohorten-Median: Donchian (Median 2,3869 PASS / Gewinner 26,3075 FAIL), Rsi2 (Median 20,3022 FAIL /
Gewinner 3,5155 PASS), TrendPullback (Median 19,6964 FAIL / Gewinner 1,6048 PASS). Promotiert wird
der Gewinner.

Root-Cause. Der Check wendete ``stop_distance_min_ratio``/``stop_distance_max_ratio`` auf
``effective_stop_ratio_cohort_median`` an; ``winner_effective_stop_ratio`` lag im selben Event
bereits vor, wurde aber nicht ausgewertet.

Fix.
1. Die blockierende Entscheidung nutzt jetzt ``winner_effective_stop_ratio``; der Kohorten-Median
   bleibt als Kontext-Telemetrie erhalten.
2. Fehlt ``winner_effective_stop_ratio``, gilt der Kohorten-Median mit ausgewiesener Begründung
   ``NO_WINNER_COHORT_FALLBACK``.
3. Neuer Check ``invariants.check_effective_stop_ratio_coverage`` (severity ``high``): FAIL unter
   80 % Abdeckung (``effective_stop_ratio_cohort_n / n_evaluable``).
"""
from automation.optimizer import invariants as inv


def _study(strategy, symbol, *, cohort_median, cohort_n, winner_ratio, n_evaluable=None):
    d = {
        "strategy": strategy, "symbol": symbol,
        "effective_stop_ratio_cohort_median": cohort_median,
        "effective_stop_ratio_cohort_n": cohort_n,
        "winner_effective_stop_ratio": winner_ratio,
    }
    if n_evaluable is not None:
        d["n_evaluable"] = n_evaluable
    return d


# ── Akzeptanzkriterium: Donchian/Rsi2/TrendPullback-Rekonstruktion ─────────────────────────────
def test_donchian_style_median_pass_winner_fail_becomes_an_offender():
    result = inv.check_effective_stop_distance([
        _study("Donchian", "TSLA.ETORO", cohort_median=2.3869, cohort_n=20, winner_ratio=26.3075),
    ], min_ratio=0.4, max_ratio=10.0)
    assert result.passed is False
    assert "Donchian/TSLA.ETORO" in result.actual
    assert result.actual["Donchian/TSLA.ETORO"]["ratio_median"] == 26.3075


def test_rsi2_style_median_fail_winner_pass_is_no_longer_an_offender():
    # Issue #1070 Fix Punkt 2/3 — ``actual`` traegt IMMER alle gemessenen Verhaeltnisse, nicht nur
    # Offender; "kein Offender" heisst hier ``result.passed is True``, nicht "Schluessel fehlt".
    result = inv.check_effective_stop_distance([
        _study("Rsi2", "TSLA.ETORO", cohort_median=20.3022, cohort_n=20, winner_ratio=3.5155),
    ], min_ratio=0.4, max_ratio=10.0)
    assert result.passed is True
    assert result.actual["Rsi2/TSLA.ETORO"]["ratio_median"] == 3.5155


def test_trendpullback_style_median_fail_winner_pass_is_no_longer_an_offender():
    result = inv.check_effective_stop_distance([
        _study("TrendPullback", "TSLA.ETORO", cohort_median=19.6964, cohort_n=20,
               winner_ratio=1.6048),
    ], min_ratio=0.4, max_ratio=10.0)
    assert result.passed is True
    assert result.actual["TrendPullback/TSLA.ETORO"]["ratio_median"] == 1.6048


def test_offender_detail_names_both_winner_and_cohort_median():
    result = inv.check_effective_stop_distance([
        _study("Donchian", "TSLA.ETORO", cohort_median=2.3869, cohort_n=20, winner_ratio=26.3075),
    ], min_ratio=0.4, max_ratio=10.0)
    offender = result.actual["Donchian/TSLA.ETORO"]
    assert offender["winner_effective_stop_ratio"] == 26.3075
    assert offender["effective_stop_ratio_cohort_median"] == 2.3869


# ── check_effective_stop_ratio_coverage ─────────────────────────────────────────────────────────
def test_flashcrash_style_low_coverage_fails():
    result = inv.check_effective_stop_ratio_coverage([
        _study("FlashCrash", "TSLA.ETORO", cohort_median=5.0, cohort_n=52, winner_ratio=5.0,
               n_evaluable=141),
    ])
    assert result.passed is False
    assert result.actual["FlashCrash/TSLA.ETORO"] < 0.8


def test_high_coverage_passes():
    result = inv.check_effective_stop_ratio_coverage([
        _study("A", "X.ETORO", cohort_median=5.0, cohort_n=120, winner_ratio=5.0,
               n_evaluable=141),
    ])
    assert result.passed is True


def test_missing_n_evaluable_is_inconclusive_not_a_fail():
    result = inv.check_effective_stop_ratio_coverage([
        _study("A", "X.ETORO", cohort_median=5.0, cohort_n=52, winner_ratio=5.0),
    ])
    assert result.evaluable is False
    assert result.inconclusive is True


def test_coverage_exactly_at_threshold_passes():
    result = inv.check_effective_stop_ratio_coverage([
        _study("A", "X.ETORO", cohort_median=5.0, cohort_n=80, winner_ratio=5.0,
               n_evaluable=100),
    ], min_coverage_fraction=0.8)
    assert result.passed is True
