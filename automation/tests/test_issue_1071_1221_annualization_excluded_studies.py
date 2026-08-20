"""Issue #1071/#1221 (P2, Katalog #1196-1221) — Akzeptanzkriterium 2: "§2.3 nennt ausgeschlossene
Studies separat".

Studies mit ``oos_n_periods_median`` unter 1/6 des Medians ihres eigenen Symbols (typischer Fall
laut Issue-Text: Squeeze mit ``n_periods≈5,0``, während andere Strategien desselben Symbols
deutlich mehr informative Perioden sehen) werden von ``report._annualization_excluded_studies``
NAMENTLICH ausgewiesen, statt unmarkiert in dieselbe Vergleichstabelle wie ihre gut besetzten
Symbol-Geschwister zu mischen — unabhängig davon, dass der Annualisierungsfaktor selbst seit
diesem Fix bar-achsen-basiert und symbolweit stabil ist (siehe
``backtest_runner._get_annualization_factor_with_source``, separat getestet in
``test_issue_1071_1221_symbol_wide_annualization.py``, dort nicht ausführbar ohne
``nautilus_trader``).
"""
from automation.optimizer.report import _annualization_excluded_studies


def _study(strategy, symbol, n_periods):
    return {"strategy": strategy, "symbol": symbol, "oos_n_periods_median": n_periods}


def test_squeeze_style_outlier_is_excluded():
    """Median UEBER ALLE VIER Studies (inkl. des Ausreissers selbst, sortiert [4.9,30,30,30])
    ist (30+30)/2=30 -> Schwelle 5.0; Squeeze mit 4.9 liegt knapp darunter."""
    studies = [
        _study("SmaCrossover", "A.ETORO", 30),
        _study("TrendPullback", "A.ETORO", 30),
        _study("AdxAtrMomentum", "A.ETORO", 30),
        _study("SqueezeBreakout", "A.ETORO", 4.9),
    ]
    excluded = _annualization_excluded_studies(studies)
    assert [e["strategy"] for e in excluded] == ["SqueezeBreakout"]
    assert excluded[0]["symbol"] == "A.ETORO"
    assert excluded[0]["oos_n_periods_median"] == 4.9
    assert excluded[0]["symbol_oos_n_periods_median"] == 30.0
    assert excluded[0]["threshold"] == 5.0


def test_value_exactly_at_the_threshold_is_not_excluded():
    studies = [
        _study("A", "X.ETORO", 30),
        _study("B", "X.ETORO", 30),
        _study("C", "X.ETORO", 5.0),  # exakt 1/6 von 30 -- NICHT < Schwelle
    ]
    assert _annualization_excluded_studies(studies) == []


def test_single_study_symbol_has_no_median_partner_and_is_never_excluded():
    studies = [_study("A", "X.ETORO", 1.0)]
    assert _annualization_excluded_studies(studies) == []


def test_missing_n_periods_is_skipped_not_crashed():
    studies = [
        {"strategy": "A", "symbol": "X.ETORO", "oos_n_periods_median": None},
        _study("B", "X.ETORO", 30),
    ]
    assert _annualization_excluded_studies(studies) == []


def test_multiple_symbols_are_evaluated_independently():
    studies = [
        _study("A", "X.ETORO", 30), _study("B", "X.ETORO", 32), _study("C", "X.ETORO", 4.0),
        _study("D", "Y.ETORO", 100), _study("E", "Y.ETORO", 90),
    ]
    excluded = _annualization_excluded_studies(studies)
    assert [e["strategy"] for e in excluded] == ["C"]
    assert excluded[0]["symbol"] == "X.ETORO"


def test_sorted_by_symbol_then_strategy():
    studies = [
        _study("Z", "B.ETORO", 30), _study("Z", "B.ETORO", 30), _study("Z", "B.ETORO", 1.0),
        _study("A", "A.ETORO", 30), _study("A", "A.ETORO", 30), _study("A", "A.ETORO", 1.0),
    ]
    excluded = _annualization_excluded_studies(studies)
    assert [(e["symbol"], e["strategy"]) for e in excluded] == [
        ("A.ETORO", "A"), ("B.ETORO", "Z")]


def test_empty_input_returns_empty_list():
    assert _annualization_excluded_studies([]) == []
