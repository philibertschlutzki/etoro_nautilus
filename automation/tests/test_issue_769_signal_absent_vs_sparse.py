"""Issue #769 (P0) — Der `STRUCTURAL_ALL_UNEVALUABLE`-Zweig urteilt weiterhin auf NULL modellierten
Trials fuer PARAMETERABHAENGIGE Kollapse; `floor_plateau_k` fehlte explizit in der Config.

Root-Cause: `binding_cause == 'signal_frequency'` warf `median_is_trades == 0` (echte
Datengeometrie, parameterunabhaengig) und `median_is_trades == 9` (die Strategie feuert bei
manchen Parametern, parameterabhaengig/tunebar) in dieselbe Kategorie — beide stoppten bei
`n_startup_trials + floor_plateau_k` (typischerweise 0 modellierte Trials).

Fix: `diagnose_trade_frequency` trennt `'signal_absent'` (median==0 UND max==0, parameter-
unabhaengig, Abbruch bleibt bei `min_for_structural` zulaessig) von `'signal_sparse'`
(parameterabhaengig, Abbruch erst ab der #768-Schwelle `min_for_zero_eligible`).

Issue #805 — `floor_plateau_k` (Default 0, die Wurzel des #805-Katalogs: `min_for_structural`
kollabierte dadurch auf EXAKT `n_startup_trials`) ist seither ENTFERNT und durch
`structural_min_modelled_trials_per_dim` (Default 3, NIE 0) ersetzt. Die Tests unten, die vorher
`floor_plateau_k: 0` setzten, sind auf den neuen Default/Key migriert — siehe
test_issue_805_structural_min_modelled.py fuer die dedizierten #805-Tests.
"""
import logging

import optuna

from automation.optimizer import run_optimization as ro
from automation.optimizer.sweep_diagnostics import diagnose_trade_frequency


def _trial(oos_evaluated=False, is_total_trades=0):
    return {"oos_evaluated": oos_evaluated, "oos_eligible": None,
            "oos_total_trades": 0, "is_total_trades": is_total_trades, "hit_trade_cap": False}


# ── diagnose_trade_frequency: signal_absent vs. signal_sparse ────────────────────────────────────
def test_signal_absent_when_is_total_trades_is_zero_across_every_trial():
    trials = [_trial(is_total_trades=0) for _ in range(16)]
    diag = diagnose_trade_frequency(trials, oos_min_trades=20)
    assert diag["binding_cause"] == "signal_absent"
    assert diag["median_is_trades"] == 0
    assert diag["max_is_trades"] == 0


def test_signal_sparse_when_some_trials_show_residual_activity():
    """median_is_trades bleibt 0 (Mehrheit feuert nie), aber MAX > 0 (manche Parameter feuern) ⇒
    parameterabhaengig, NICHT 'signal_absent'."""
    trials = [_trial(is_total_trades=0) for _ in range(12)] + [
        _trial(is_total_trades=9) for _ in range(4)
    ]
    diag = diagnose_trade_frequency(trials, oos_min_trades=20)
    assert diag["binding_cause"] == "signal_sparse"
    assert diag["median_is_trades"] == 0
    assert diag["max_is_trades"] == 9


def test_signal_sparse_when_median_is_nonzero_but_below_threshold():
    trials = [_trial(is_total_trades=9) for _ in range(16)]
    diag = diagnose_trade_frequency(trials, oos_min_trades=20)
    assert diag["binding_cause"] == "signal_sparse"
    assert diag["median_is_trades"] == 9
    assert diag["max_is_trades"] == 9


def test_hold_duration_unaffected_by_the_split():
    trials = [_trial(is_total_trades=100) for _ in range(4)] + [
        {**_trial(is_total_trades=100), "hit_trade_cap": True} for _ in range(12)
    ]
    diag = diagnose_trade_frequency(trials, oos_min_trades=20)
    assert diag["binding_cause"] == "hold_duration"


# ── floor_plateau_callback: signal_sparse stoppt NICHT bei n_startup, sondern bei der #768-Schwelle
class _FakeTrial:
    def __init__(self, value, *, oos_evaluated=False, is_total_trades=0):
        self.value = value
        self.state = optuna.trial.TrialState.COMPLETE
        self.user_attrs = {"oos_evaluated": oos_evaluated, "is_total_trades": is_total_trades}


class _FakeStudy:
    def __init__(self, trials):
        self.trials = trials
        self._attrs = {}
        self.stopped = False

    @property
    def user_attrs(self):
        return dict(self._attrs)

    def set_user_attr(self, k, v):
        self._attrs[k] = v

    def stop(self):
        self.stopped = True


def _quiet_logger(name):
    lg = logging.getLogger(name)
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    lg.propagate = False
    return lg


def test_signal_sparse_does_not_stop_at_n_startup_only_at_zero_eligible_threshold():
    # W hat kein plateau_min_modelled_trials_per_dim ⇒ Basis bleibt max(32, 2*16)=32 ⇒
    # min_for_zero_eligible = 16+32 = 48. Issue #805 — floor_plateau_k entfernt; ohne
    # structural_min_modelled_trials_per_dim-Key greift der Default 3 ⇒ min_for_structural = 19
    # (irrelevant fuer diesen Test: signal_sparse urteilt ohnehin ueber min_for_zero_eligible).
    W = {"n_startup_trials": 16}
    trials = [_FakeTrial(-9.8, is_total_trades=5) for _ in range(20)]  # >= min_for_structural(19)
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                              logger=_quiet_logger("t769_no_stop_at_16"), stop_on_plateau=True)
    assert study.stopped is False, "signal_sparse darf NICHT bei n_startup(+structural_extra) stoppen (Root-Cause #769)"


def test_signal_sparse_stops_at_the_768_threshold():
    W = {"n_startup_trials": 16}
    trials = [_FakeTrial(-9.8, is_total_trades=5) for _ in range(48)]  # == min_for_zero_eligible
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                              logger=_quiet_logger("t769_stop_at_48"), stop_on_plateau=True)
    assert study.stopped is True


def test_signal_absent_still_stops_at_the_narrow_structural_threshold():
    """median_is_trades==0 UND max==0 ⇒ Abbruch bleibt bei der ENGEN STRUCTURAL-Schwelle zulaessig
    (kein unnoetiges Budget fuer echte Datengeometrie) — seit #805 ist diese Schwelle
    n_startup_trials + structural_min_modelled_trials_per_dim-abgeleiteter Zuschlag (hier explizit 1,
    ohne strategy=... ⇒ flach) statt des entfernten floor_plateau_k=0 (die Schwelle darf seit #805
    nie mehr EXAKT n_startup_trials sein, siehe test_issue_805_structural_min_modelled.py)."""
    W = {"n_startup_trials": 16, "structural_min_modelled_trials_per_dim": 1}
    trials = [_FakeTrial(-9.8, is_total_trades=0) for _ in range(17)]
    study = _FakeStudy(trials)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                              logger=_quiet_logger("t769_absent_stop"), stop_on_plateau=True)
    assert study.stopped is True


def test_structural_min_modelled_trials_per_dim_replaces_floor_plateau_k_in_real_config():
    """Akzeptanzkriterium #805: floor_plateau_k existiert nicht mehr in der ausgelieferten Config;
    structural_min_modelled_trials_per_dim ist gesetzt und STRIKT > 0 (nie wieder der degenerierte
    0-Wert, der #805 ausloeste)."""
    import json
    from automation.optimizer.trial_config import config_dir
    real_cfg = json.loads((config_dir() / "optimizer.json").read_text("utf-8"))
    assert "floor_plateau_k" not in real_cfg
    assert real_cfg.get("structural_min_modelled_trials_per_dim", 0) > 0
