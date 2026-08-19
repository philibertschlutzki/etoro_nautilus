"""Issue #996/#1148 (Katalog #1170) — ``check_trailing_stop_loss_share``: Bedingung 2 faellt
lautlos aus.

Symptom: der blockierende Check failte in 27 von 28 Studies AUSSCHLIESSLICH ueber Bedingung 1
(``loss_share > 0.60``). Bedingung 2 (``mean_loss_ratio > 1.25``) wurde 0x ausgewertet, weil ihr
Zaehler (``gross_loss_median_bps_trailing_stop``, seit #972/#1126 der robuste Median) ``None`` war
und ``if mean_loss_ts is not None and mean_loss_all:`` sie kommentarlos uebersprang.

Fix: ``conditions_evaluated``/``conditions_violated`` je Study telemetriert; fehlt der Zaehler von
Bedingung 2 in > 50 % der Kandidaten-Studies, ist das GESAMTERGEBNIS ``evaluable=False``/
``passed=None`` (dieselbe #995/#1147-Tri-State-Mechanik) statt eines Urteils allein auf Bedingung 1.
"""
from automation.optimizer import invariants as inv


def _study(label, n_exits, n_losses, median_loss_ts=None, median_loss_all=None):
    strategy, symbol = label.split("/")
    return {
        "strategy": strategy, "symbol": symbol,
        "exit_reason_histogram": {"TRAILING_STOP": n_exits},
        "oos_n_trailing_stop_losses": n_losses,
        "gross_loss_median_bps_trailing_stop": median_loss_ts,
        # Issue #1024/#1173 (Pitfall #423) — der Nenner ist seit diesem Fix ebenfalls ein Median.
        "gross_loss_median_bps": median_loss_all,
    }


def test_reproduces_symptom_condition2_numerator_missing_everywhere_is_not_evaluable():
    """27/28-Reproduktion: Bedingung 1 verletzt ueberall, Bedingung 2 hat NIRGENDS einen Zaehler."""
    studies = [_study(f"A/S{i}.ETORO", 100, 70) for i in range(5)]
    result = inv.check_trailing_stop_loss_share(studies)
    assert result.evaluable is False
    assert result.passed is None
    assert result.severity == "blocking"
    assert result.evaluability["n_candidates"] == 5
    assert result.evaluability["n_measured"] == 0
    for label, entry in result.actual.items():
        assert entry["conditions_evaluated"] == ["loss_share"]
        assert entry["conditions_violated"] == ["loss_share"]


def test_condition2_available_for_majority_is_evaluable_and_still_fails_normally():
    studies = [_study(f"A/S{i}.ETORO", 100, 70, median_loss_ts=200.0, median_loss_all=50.0)
               for i in range(5)]
    result = inv.check_trailing_stop_loss_share(studies)
    assert result.evaluable is True
    assert result.passed is False
    for entry in result.actual.values():
        assert entry["conditions_evaluated"] == ["loss_share", "median_loss_ratio"]
        assert set(entry["conditions_violated"]) == {"loss_share", "median_loss_ratio"}


def test_condition2_missing_in_exactly_half_is_still_evaluable():
    """> 50 % (strikt), nicht >= 50 % — 50/50 bleibt evaluable."""
    studies = [
        _study("A/S0.ETORO", 100, 70, median_loss_ts=200.0, median_loss_all=50.0),
        _study("A/S1.ETORO", 100, 70),
    ]
    result = inv.check_trailing_stop_loss_share(studies)
    assert result.evaluable is True


def test_condition2_missing_in_majority_but_not_all_is_not_evaluable():
    studies = [
        _study("A/S0.ETORO", 100, 70, median_loss_ts=200.0, median_loss_all=50.0),
        _study("A/S1.ETORO", 100, 70),
        _study("A/S2.ETORO", 100, 70),
    ]
    result = inv.check_trailing_stop_loss_share(studies)
    assert result.evaluable is False
    assert result.passed is None


def test_detail_names_the_actually_firing_condition_per_offender():
    studies = [
        # Nur Bedingung 1 verletzt.
        _study("A/S0.ETORO", 100, 70, median_loss_ts=10.0, median_loss_all=50.0),
        # Nur Bedingung 2 verletzt.
        _study("A/S1.ETORO", 100, 10, median_loss_ts=200.0, median_loss_all=50.0),
    ]
    result = inv.check_trailing_stop_loss_share(studies)
    assert result.evaluable is True
    assert result.actual["A/S0.ETORO"]["conditions_violated"] == ["loss_share"]
    assert result.actual["A/S1.ETORO"]["conditions_violated"] == ["median_loss_ratio"]
    assert "ausschliesslich ueber Bedingung 1" in result.detail
    assert "ausschliesslich ueber Bedingung 2" in result.detail


def test_no_candidates_is_a_clean_vacuous_pass():
    result = inv.check_trailing_stop_loss_share([])
    assert result.passed is True
    assert result.evaluable is True


def test_evaluable_false_makes_a_blocking_check_a_finding_for_decision_admissible():
    """Konsistenzcheck: die #995/#1147-Verdrahtung greift automatisch, weil dieser Check bereits
    severity='blocking' traegt."""
    from automation.optimizer import report as rpt
    studies = [_study(f"A/S{i}.ETORO", 100, 70) for i in range(5)]
    result = inv.check_trailing_stop_loss_share(studies).to_dict()
    assert rpt._compute_decision_admissible([result]) is False
