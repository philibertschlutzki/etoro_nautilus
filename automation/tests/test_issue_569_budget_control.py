"""Issue #568 — Trial-Budget-Steuerung: gradienten-getriebene Eskalation + dimensionsgekoppelte Startups.

100 → 200 Trials lieferten identische best_value: auf einer flachen Landschaft ist zusätzliches
Budget wirkungslos. Fix: (1) n_startup_trials an die Parameterzahl koppeln (n_startup ≥ k·dim),
(2) ein Gradienten-Gate, das höheres Budget nur bei Signal (evaluable_fraction>0 ∧ pstdev(reward)>τ)
rechtfertigt und den Entscheid je Study telemetriert/loggt.

Akzeptanzkriterien (Issue #568):
- Studies ohne Signal (evaluable_fraction·pstdev unter Schwelle) werden nicht eskaliert (Log-Nachweis).
- n_startup_trials skaliert dokumentiert mit der Parameterzahl.
"""
import json
import time
from pathlib import Path

from automation.optimizer.run_optimization import (
    derive_n_startup_trials, study_shows_gradient_signal, _emit_study_summary,
)

OPT = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))


# ── n_startup_trials koppelt an die Dimensionalität ──────────────────────────────────────────
def test_n_startup_scales_with_param_count():
    k = 2
    # SmaCrossover: 2 Parameter ⇒ max(16, ceil(2·2)=4) = 16 (Basiswert dominiert).
    assert derive_n_startup_trials("SmaCrossoverStrategy", 16, {"n_startup_trials_per_dim": k}) == 16
    # ComboTrendVwap: 14 Parameter ⇒ max(16, ceil(2·14)=28) = 28 (dim dominiert).
    assert derive_n_startup_trials("ComboTrendVwapStrategy", 16, {"n_startup_trials_per_dim": k}) == 28
    # FlashCrash: 8 Parameter ⇒ max(16, ceil(2·8)=16) = 16.
    assert derive_n_startup_trials("FlashCrashReversalStrategy", 16, {"n_startup_trials_per_dim": k}) == 16


def test_n_startup_legacy_without_key_is_base():
    """Fehlt n_startup_trials_per_dim ⇒ fixer Basiswert (bit-identisch, Zero-Hardcoding)."""
    assert derive_n_startup_trials("ComboTrendVwapStrategy", 16, {}) == 16
    assert derive_n_startup_trials("ComboTrendVwapStrategy", 16, {"n_startup_trials_per_dim": 0}) == 16


def test_n_startup_monotone_in_k():
    a = derive_n_startup_trials("ComboTrendVwapStrategy", 16, {"n_startup_trials_per_dim": 1})
    b = derive_n_startup_trials("ComboTrendVwapStrategy", 16, {"n_startup_trials_per_dim": 3})
    assert b > a  # mehr Startpunkte je Dimension ⇒ mehr Startup-Trials


# ── Gradienten-Gate für die Eskalation ───────────────────────────────────────────────────────
def test_flat_landscape_shows_no_signal():
    """Deckel/Plateau: identische Rewards ⇒ kein Signal ⇒ keine Eskalation gerechtfertigt."""
    assert study_shows_gradient_signal([5.0, 5.0, 5.0, 5.0], evaluable_fraction=1.0, tau=1e-3) is False


def test_informative_landscape_shows_signal():
    assert study_shows_gradient_signal([1.0, 2.5, 4.0, 5.5], evaluable_fraction=1.0, tau=1e-3) is True


def test_no_evaluable_trials_no_signal():
    """evaluable_fraction=0 (alle unevaluable) ⇒ nie eskalieren, egal wie die Rewards streuen."""
    assert study_shows_gradient_signal([-9.9, -9.7, -9.8], evaluable_fraction=0.0, tau=1e-3) is False


def test_degenerate_inputs_no_signal():
    assert study_shows_gradient_signal([], 1.0, 1e-3) is False
    assert study_shows_gradient_signal([3.0], 1.0, 1e-3) is False  # < 2 Trials


# ── _emit_study_summary weist das Gradienten-Signal aus (Log-Nachweis der Nicht-Eskalation) ──
class _DummyTrial:
    def __init__(self, value, evaluable, eligible=None):
        self.value = value
        # Issue #640 — gradient_signal misst seither die FEASIBLE-Region (oos_eligible), nicht mehr
        # oos_evaluated. Default: eligible == evaluable (die alten Fixtures meinten "evaluable" im
        # Sinne von "zaehlt fuers Signal", was nach #640 oos_eligible ist).
        self.user_attrs = {
            "oos_evaluated": evaluable,
            "oos_eligible": evaluable if eligible is None else eligible,
            "backtest_ms": 10,
        }


class _DummyStudy:
    def __init__(self, values, evaluable, eligible=None):
        self.study_name = "study_X"
        elig = eligible or evaluable
        self.trials = [_DummyTrial(v, e, el) for v, e, el in zip(values, evaluable, elig)]
        self.best_value = max(values)


def test_study_summary_emits_gradient_signal(monkeypatch):
    import automation.optimizer.run_optimization as ro
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    captured = []
    monkeypatch.setattr(ro, "emit_execution_event",
                        lambda logger, name, payload: captured.append((name, payload)))
    # Flache Study (Deckel): alle Rewards identisch, alle eligible ⇒ gradient_signal False.
    flat = _DummyStudy([5.0, 5.0, 5.0], [True, True, True])
    _emit_study_summary(flat, "TSLA.ETORO", time.perf_counter())
    name, payload = captured[-1]
    assert name == "optimizer_study_completed"
    assert payload["gradient_signal"] is False
    assert payload["feasible_reward_pstdev"] == 0.0
    assert payload["feasible_p_eligible"] == 1.0
    # Issue #640 — die globale Roh-Diagnose bleibt zusaetzlich erhalten, ist aber NICHT mehr die
    # Eskalations-Grundlage.
    assert payload["reward_pstdev"] == 0.0
    assert payload["evaluable_fraction"] == 1.0

    # Informative Study (Streuung IM FEASIBLEN Bereich) ⇒ gradient_signal True.
    captured.clear()
    signal = _DummyStudy([1.0, 3.0, 5.0], [True, True, True])
    _emit_study_summary(signal, "AAA.ETORO", time.perf_counter())
    assert captured[-1][1]["gradient_signal"] is True


def test_global_variance_alone_does_not_trigger_gradient_signal(monkeypatch):
    """Issue #640 — Regressionstest fuer den Kern der Aenderung: eine Study mit GROSSER globaler
    Streuung (Populations-Mischung aus Unevaluable/Failure/Eligible), aber KONSTANTEM Reward
    INNERHALB der eligiblen Kohorte, darf NICHT als Gradienten-Signal gelten — vor #640 haette der
    globale pstdev (dominiert von der Gate-Passrate) hier faelschlich True geliefert."""
    import automation.optimizer.run_optimization as ro
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    captured = []
    monkeypatch.setattr(ro, "emit_execution_event",
                        lambda logger, name, payload: captured.append((name, payload)))

    class _T:
        def __init__(self, value, eligible, evaluated=True):
            self.value = value
            self.user_attrs = {"oos_evaluated": evaluated, "oos_eligible": eligible, "backtest_ms": 5}

    class _S:
        study_name = "study_mixed"
        # Grosse globale Streuung (Unevaluable ~ -20, Failure ~ -50, Eligible konstant 2.0), aber
        # die eligiblen Trials selbst sind IDENTISCH ⇒ kein Signal im feasiblen Bereich.
        trials = [_T(-20.0, False), _T(-50.0, False), _T(-19.5, False),
                  _T(2.0, True), _T(2.0, True), _T(2.0, True)]
        best_value = 2.0

    _emit_study_summary(_S(), "MIX.ETORO", time.perf_counter())

    payload = captured[-1][1]
    assert payload["gradient_signal"] is False
    assert payload["feasible_reward_pstdev"] == 0.0
    # Die globale Streuung ist gross — genau der Wert, den #640 NICHT mehr als Grundlage nutzt.
    assert payload["reward_pstdev"] > 1.0


# ── Produktions-Config hat die Kopplung + Schwelle deklariert ────────────────────────────────
def test_production_config_declares_budget_keys():
    assert OPT.get("n_startup_trials_per_dim", 0) >= 1
    assert "tier_escalation_min_signal" in OPT
