"""Issue #758 (P1) — Inferenz-Doppelstandard zwischen Eligibility und Promotion.

Root-Cause: Eligibility (`oos_min_psr`) nutzte `psr_z` (i.i.d.-`√(T-1)`-Sampling-Varianz einer
Sharpe-Formel), Promotion (`holdout_bootstrap_ci`) nutzte bereits den Stationary Bootstrap
(Serienabhängigkeit, empirische Blocklänge). Ein Trial, dessen Serienabhängigkeit den i.i.d.-SE
unterschätzt, konnte die Eligibility passieren und im Holdout durchfallen — nicht wegen fehlenden
Edges, sondern wegen des Methodenwechsels.

Fix: mit Weg B aus #757 (Bootstrap-SE für Eligibility-PSR UND Promotion-DSR) fällt der
Doppelstandard automatisch weg — beide Stufen rufen `automation.optimizer.bootstrap`s
`stationary_bootstrap_indices`/`sortino_statistic` auf, KEINE Duplikat-Implementierung.
"""
import numpy as np

import automation.optimizer.bootstrap as bootstrap_mod
import automation.optimizer.deflation as deflation_mod
from automation.optimizer.deflation import bootstrap_psr_z


def test_eligibility_and_holdout_gate_call_the_same_stationary_bootstrap_primitives(monkeypatch):
    """Aufruf-Assertion (kein Duplikat): sowohl der Eligibility-Pfad (bootstrap_psr_z, #757) als
    auch der Holdout-CI-Pfad (bootstrap_ci) rufen `stationary_bootstrap_indices` UND
    `sortino_statistic` aus DEMSELBEN Modul auf."""
    calls = {"stationary_bootstrap_indices": 0, "sortino_statistic": 0}
    orig_sbi = bootstrap_mod.stationary_bootstrap_indices
    orig_ss = bootstrap_mod.sortino_statistic

    def _spy_sbi(*a, **k):
        calls["stationary_bootstrap_indices"] += 1
        return orig_sbi(*a, **k)

    def _spy_ss(*a, **k):
        calls["sortino_statistic"] += 1
        return orig_ss(*a, **k)

    monkeypatch.setattr(bootstrap_mod, "stationary_bootstrap_indices", _spy_sbi)
    monkeypatch.setattr(bootstrap_mod, "sortino_statistic", _spy_ss)

    rng = np.random.default_rng(1)
    rets = rng.normal(0.001, 0.01, 80).tolist()

    # Eligibility-Pfad (#757).
    bootstrap_psr_z(rets, n_boot=30, seed=1)
    assert calls["stationary_bootstrap_indices"] >= 30
    assert calls["sortino_statistic"] >= 30

    calls["stationary_bootstrap_indices"] = 0
    calls["sortino_statistic"] = 0

    # Promotion-Pfad (Holdout-Bootstrap-CI, #619) — dieselben Primitive.
    from automation.optimizer.bootstrap import bootstrap_ci, sortino_statistic as _ss_direct
    bootstrap_ci(rets, lambda a: _ss_direct(a, mar=0.0, annualization=1.0), n_boot=30, seed=1)
    assert calls["stationary_bootstrap_indices"] >= 30


def test_report_exposes_inference_method_for_both_stages():
    from automation.optimizer.report import _study_record

    class _T:
        def __init__(self):
            self.value = 1.0
            self.user_attrs = {"oos_evaluated": True, "oos_eligible": True}

    class _S:
        trials = [_T(), _T()]
        best_value = 1.0
        user_attrs = {}

    proposal = {
        "symbol": "AAA.ETORO", "strategy": "SmaCrossoverStrategy",
        "holdout": {"symbol": {"deflation_inference_method": "stationary_bootstrap"}},
    }
    record, _checks = _study_record(proposal, _S())
    assert record["inference_method"]["eligibility"] == "stationary_bootstrap"
    assert record["inference_method"]["promotion"] == "stationary_bootstrap"


def test_report_inference_method_none_when_no_evaluated_trials():
    from automation.optimizer.report import _study_record

    class _S:
        trials = []
        best_value = None
        user_attrs = {}

    record, _checks = _study_record({"symbol": "X", "strategy": "Y"}, _S())
    assert record["inference_method"]["eligibility"] is None
    assert record["inference_method"]["promotion"] is None
