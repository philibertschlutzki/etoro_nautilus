"""Issue #1092/#1240 (P1) — ``deflation_n_effective`` aus einer Quelle; ``deflation_n_source``
gestempelt; ``check_n_family_consistency`` von ``medium`` auf ``high`` gehoben.

Root-Cause. ``check_n_family_consistency`` (invariants.py) verifiziert, dass die exportierte
Telemetrie ``deflation_n_eligible``/``deflation_n_family_effective``/``deflation_n_effective`` der
Formel ``deflation_n_effective == max(deflation_n_eligible, deflation_n_family_effective)`` genügt.
confirm.py's ``deflation_n_effective`` wird EINMALIG aus der bare ``deflation_n``-Variable
berechnet (``deflation_n_effective = max(deflation_n, deflation_n_family_effective)``) — die
#865-``per_stratum``-Heterogenitätspolitik reassigned diese SELBE bare Variable ANSCHLIESSEND
(``deflation_n = deflation_stratum_n``) für einen ANDEREN, absichtlich entkoppelten Zweck (die
Varianzschätzung, siehe ``deflation.sr0_multiple_testing_robust``s ``n_trials``/
``variance_n_trials``-Docstring — die Multiplizität selbst bleibt bewusst UNGESCHMÄLERT durch die
Stratum-Einengung). Die spätere Telemetrie-Stempelung ``metrics_symbol["deflation_n_eligible"] =
deflation_n`` griff auf die INZWISCHEN reassignte (engere) Stratum-Zahl zu, während
``deflation_n_effective`` weiter die Zahl aus der URSPRÜNGLICHEN, breiteren Kohorte trug — genau
der #652/#670-Widerspruch, den ``check_n_family_consistency`` erkennen soll.

Fix. ``deflation_n_eligible_at_effective`` friert die bare ``deflation_n``-Variable UNMITTELBAR
NACH der ``deflation_n_effective``-Berechnung ein (bevor die #865-Politik sie umwidmen kann);
``metrics_symbol["deflation_n_eligible"]`` wird ab jetzt aus diesem eingefrorenen Snapshot gespeist
statt aus der bare Variable. ``deflation_n_source`` (``n_eligible``/``n_family_stage1_per_strategy``/
``max_of_both``) macht zusätzlich sichtbar, welche Seite des max() tatsächlich gewonnen hat.
``check_n_family_consistency`` traegt jetzt ``severity='high'`` (vorher ``medium``) — eine
abweichende Multiplizitaet veraendert die Promotionsschwelle direkt.
"""
from automation.optimizer import confirm
from automation.optimizer import invariants as inv
from automation.tests.test_issue_865_deflation_heterogeneity_policy import (
    _isolate, _cohort_factory, _holdout_factory, _result_payload, ro,
)


def test_per_stratum_narrowing_keeps_n_eligible_telemetry_consistent_with_n_effective(
        tmp_path, monkeypatch):
    """Primärreproduktion: eine grosse, heterogene Kohorte (6 Trials, zwei n_periods-Cluster von
    je 3), die unter der 'per_stratum'-Politik erfolgreich auf ein 3-Trial-Stratum verengt wird.
    Vor #1092 zeigte ``deflation_n_eligible`` die verengte Stratum-Zahl (3), waehrend
    ``deflation_n_effective`` weiterhin die Zahl der VOLLEN Kohorte (6) trug --
    ``check_n_family_consistency`` FIEL faelschlich, obwohl Entscheidung und Telemetrie in
    Wirklichkeit dieselbe Quelle konsumiert hatten."""
    _isolate(monkeypatch, tmp_path)
    global_params = {"sma_period": 20}
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([
        (0.10, 190), (0.12, 200), (0.09, 210), (0.5, 8900), (0.4, 9000), (0.6, 9100),
    ]))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=6)

    symbol_result = _result_payload(sortino_ratio=0.3, dd=0.05, sortino_period=0.02, n_periods=200)
    global_result = _result_payload(sortino_ratio=0.1, dd=0.05, sortino_period=0.01, n_periods=200)

    res = confirm.confirm_per_symbol_promotion(
        study, "SmaCrossoverStrategy", "AAA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=symbol_result,
                                      global_result=global_result),
    )
    metrics = res["metrics_symbol"]
    assert metrics.get("deflated_sr0") is not None
    # Das Stratum selbst ist auf < 6 Trials verengt (die #865-Kernaussage; siehe
    # test_large_heterogeneous_cohort_recovers_dsr_via_per_stratum für dieselbe Fixture) ...
    assert 2 <= metrics.get("deflation_stratum_n") < 6
    # ... aber die Multiplizitaets-Telemetrie zeigt weiterhin die VOLLE Kohorte (6), nicht die
    # verengte Stratum-Zahl -- der eigentliche #1092-Fix.
    assert metrics.get("deflation_n_eligible") == 6
    assert metrics.get("deflation_n_effective") == 6
    assert metrics.get("deflation_n_source") == "n_eligible"

    coherence = inv.check_n_family_consistency(metrics)
    assert coherence.passed is True, coherence.detail
    assert coherence.severity == "high"


def test_deflation_n_source_reflects_family_win(tmp_path, monkeypatch):
    """Dominiert die Familien-Multiplizitaet (deflation_n_family_effective > deflation_n),
    stempelt deflation_n_source 'n_family_stage1_per_strategy', nicht 'n_eligible'."""
    _isolate(monkeypatch, tmp_path)
    global_params = {"price_breakout_period": 20}
    monkeypatch.setattr(ro, "run_backtest", _cohort_factory([(0.02, 200), (0.025, 200)]))
    study = ro.optimize_symbol("DynamicBreakoutStrategy", "TSLA.ETORO", n_trials=2)

    fixed_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)
    res = confirm.confirm_per_symbol_promotion(
        study, "DynamicBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=fixed_result,
                                      global_result=fixed_result),
        deflation_n_family=50,  # >> deflation_n (2) ⇒ die Familien-Seite gewinnt den max().
    )
    metrics = res["metrics_symbol"]
    assert metrics.get("deflation_n_effective") == 50
    assert metrics.get("deflation_n_source") == "n_family_stage1_per_strategy"

    coherence = inv.check_n_family_consistency(metrics)
    assert coherence.passed is True, coherence.detail


def test_deflation_n_source_reflects_tie():
    """deflation_n_eligible == deflation_n_family_effective ⇒ 'max_of_both' (beide Seiten binden
    gleichzeitig, keine der beiden 'gewinnt' eindeutig)."""
    result = inv.check_n_family_consistency({
        "deflation_n_eligible": 10, "deflation_n_family_effective": 10,
        "deflation_n_effective": 10, "deflation_n_source": "max_of_both",
    })
    assert result.passed is True
    assert result.severity == "high"


def test_n_family_consistency_severity_is_high_on_mismatch():
    """Regressionsschutz Fix Punkt 3: severity ist 'high' (nicht mehr 'medium'), unabhaengig vom
    Verdikt."""
    passing = inv.check_n_family_consistency({
        "deflation_n_eligible": 40, "deflation_n_family_effective": 65,
        "deflation_n_effective": 65,
    })
    assert passing.severity == "high"
    failing = inv.check_n_family_consistency({
        "deflation_n_eligible": 40, "deflation_n_family_effective": 65,
        "deflation_n_effective": 40,
    })
    assert failing.severity == "high"
    not_applicable = inv.check_n_family_consistency({})
    assert not_applicable.severity == "high"
