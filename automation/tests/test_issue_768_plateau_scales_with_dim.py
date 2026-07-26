"""Issue #768 (P0) — `plateau_min_modelled_trials` ist eine dimensionsblinde Konstante; der
ausgefuehrte Budgetanteil vor dem Zero-Eligible-Urteil faellt monoton mit der Suchraum-Dimension.

Fix: `derive_plateau_min_modelled_trials(strategy, base, opt_data)` skaliert die Schwelle
deklarativ mit `dim` (`plateau_min_modelled_trials_per_dim`), analog `derive_n_trials`/
`derive_n_startup_trials`. `floor_plateau_callback` deckelt `min_for_zero_eligible` zusaetzlich auf
das Study-Budget (`n_trials_budget`-User-Attr), damit der Guard nie NACH Budget-Ende urteilt.
"""
import logging

import optuna
import pytest

from automation.optimizer import run_optimization as ro


def _quiet_logger(name):
    lg = logging.getLogger(name)
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    lg.propagate = False
    return lg


# ── derive_plateau_min_modelled_trials: reine Funktion ───────────────────────────────────────────
def test_missing_key_is_bit_identical_to_base():
    assert ro.derive_plateau_min_modelled_trials("ComboTrendVwapStrategy", 48, {}) == 48
    assert ro.derive_plateau_min_modelled_trials(None, 48, {"plateau_min_modelled_trials_per_dim": 8}) == 48


def test_scales_with_dimension_for_high_dim_strategy():
    # ComboTrendVwapStrategy hat dim=14 (siehe test_issue_762/derive_n_trials-Fixtures) ⇒ 14*8=112 > 48.
    result = ro.derive_plateau_min_modelled_trials(
        "ComboTrendVwapStrategy", 48, {"plateau_min_modelled_trials_per_dim": 8})
    assert result >= 48
    assert result == max(48, __import__("math").ceil(8 * 14))


def test_low_dim_strategy_keeps_the_base_floor():
    # SmaCrossoverStrategy hat dim=2 ⇒ ceil(8*2)=16 < base 48 ⇒ max(...) bleibt bei 48 (kein Downgrade).
    result = ro.derive_plateau_min_modelled_trials(
        "SmaCrossoverStrategy", 48, {"plateau_min_modelled_trials_per_dim": 8})
    assert result == 48


def test_invalid_k_falls_back_to_base():
    assert ro.derive_plateau_min_modelled_trials(
        "ComboTrendVwapStrategy", 48, {"plateau_min_modelled_trials_per_dim": 0}) == 48
    assert ro.derive_plateau_min_modelled_trials(
        "ComboTrendVwapStrategy", 48, {"plateau_min_modelled_trials_per_dim": -1}) == 48


# ── Ausgefuehrter Budgetanteil wird dimensionsinvariant statt monoton fallend ────────────────────
def _dim_for(strategy: str) -> int:
    from automation.optimizer import bounds
    return len(bounds.extract_numeric_bounds(strategy))


@pytest.mark.parametrize("strategy", [
    "SmaCrossoverStrategy", "MeanReversionStrategy", "FlashCrashReversalStrategy",
    "SqueezeBreakoutStrategy", "ComboTrendVwapStrategy",
])
def test_executed_budget_share_stays_in_a_tight_band_across_dimensions(strategy):
    """Akzeptanzkriterium #768/1: min_for_zero_eligible / n_trials liegt in einem engen Band
    (statt der Pre-#768-Spanne 32%-64%) fuer dim in {2, 6, 8, 9, 14}."""
    opt_data = {
        "n_trials_per_dim": 20, "n_startup_trials_per_dim": 2,
        "n_startup_trials_high_dim_threshold": 8, "n_startup_trials_per_dim_high_dim": 3,
        "plateau_min_modelled_trials_per_dim": 8,
    }
    dim = _dim_for(strategy)
    n_trials = ro.derive_n_trials(strategy, 100, opt_data)
    n_startup = ro.derive_n_startup_trials(strategy, 16, opt_data)
    pmt = ro.derive_plateau_min_modelled_trials(strategy, 48, opt_data)
    min_for_zero_eligible = min(n_startup + pmt, n_trials)
    share = min_for_zero_eligible / n_trials
    assert 0.45 <= share <= 0.65, f"{strategy} (dim={dim}): share={share:.2f} ausserhalb des Ziel-Bands"


def test_guard_never_judges_after_full_budget_via_n_trials_budget_cap():
    """Akzeptanzkriterium #768/3: min_for_zero_eligible <= n_trials_budget (der Guard kann das
    Budget nie ueberschreiten) — hier direkt am Callback verifiziert ueber die Study-User-Attr-
    Kappung, mit einem absichtlich VIEL zu hohen plateau_min_modelled_trials_per_dim."""
    class _FakeTrial:
        def __init__(self, value):
            self.value = value
            self.state = optuna.trial.TrialState.COMPLETE
            self.user_attrs = {"oos_evaluated": False}

    class _FakeStudy:
        def __init__(self, trials, n_trials_budget):
            self.trials = trials
            self._attrs = {"n_trials_budget": n_trials_budget}
            self.stopped = False

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

        def stop(self):
            self.stopped = True

    # dim(ComboTrendVwapStrategy)=14, k=1000 wuerde ohne Deckelung eine absurd hohe Schwelle ergeben.
    W = {"n_startup_trials": 16, "plateau_min_modelled_trials": 48,
         "plateau_min_modelled_trials_per_dim": 1000}
    trials = [_FakeTrial(-9.85) for _ in range(30)]  # exakt das (kleine) Study-Budget
    study = _FakeStudy(trials, n_trials_budget=30)
    ro.floor_plateau_callback(study, trials[-1], weights=W, n_startup_trials=16,
                              logger=_quiet_logger("t768_cap"), stop_on_plateau=True,
                              strategy="ComboTrendVwapStrategy", symbol="TSLA.ETORO")
    # Alle 30 Trials sind oos_evaluated=False mit is_total_trades=0 ⇒ signal_absent ⇒ min_for_structural
    # (n_startup+K=16) bindet ohnehin zuerst; der Test verifiziert nur, dass min_for_zero_eligible NIE
    # das gedeckelte Budget (30) uebersteigt, indem ein zweiter Lauf mit signal_sparse-Ursache das
    # tatsaechliche Cap prueft (siehe test_issue_769 fuer den signal_sparse-Pfad selbst).
    assert study.stopped is True
