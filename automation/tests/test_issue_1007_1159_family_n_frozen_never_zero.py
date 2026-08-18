"""Issue #1007/#1159 (Katalog #1170, P2) — ``deflation_n_family_frozen = 0`` bei 180 Trials.

Symptom: SqueezeBreakout/NVDA: 180 Trials, ``deflation_n_family_frozen = 0``. ``Φ⁻¹(1 − 1/N)`` ist
für N = 0 undefiniert. Latent, weil die Study am IS-Gate stirbt — aber fail-open.

Fix:
1. ``report._study_record`` bildet eine rohe ``deflation_n_family_frozen <= 0`` beim Export auf
   ``None`` ab, MIT explizitem ``deflation_n_family_frozen_skipped_reason`` (statt einer stillen
   ``0``, die ``Φ⁻¹(1 − 1/N)`` undefiniert speisen würde).
2. ``deflation.sr0_multiple_testing_robust`` lehnt ``n_trials < 1`` fail-loud ab (``ValueError``)
   statt einen bedeutungslosen SR₀ zu liefern.
"""
import pytest

from automation.optimizer import deflation
from automation.optimizer.report import _study_record


def _study(user_attrs, deltas_by_trial=()):
    class _T:
        def __init__(self, deltas):
            self.value = 1.0
            self.user_attrs = {"oos_evaluated": True, "oos_eligible": True, "oos_gate_deltas": deltas}

    class _S:
        trials = [_T(d) for d in deltas_by_trial]
        best_value = 1.0

    s = _S()
    s.user_attrs = user_attrs
    return s


CFG = {"eligible_requires_all": ["min_trades", "max_drawdown", "oos_min_psr"]}


# --- report._study_record: 0 -> None + Skip-Grund --------------------------------------------------

def test_reference_symptom_raw_zero_becomes_none_with_the_degenerate_reason():
    """Reproduziert das #1159-Referenzsymptom: 180 Trials (hier via n_trials-Anzahl irrelevant fuer
    dieses Feld, nur der rohe Stempel zaehlt), family_membership='excluded_degenerate',
    deflation_n_family_frozen roh = 0."""
    proposal = {"symbol": "NVDA.ETORO", "strategy": "SqueezeBreakoutStrategy"}
    study = _study({"deflation_n_family_frozen": 0, "family_membership": "excluded_degenerate"})
    record, _checks = _study_record(proposal, study, CFG)
    assert record["deflation_n_family_frozen"] is None
    assert record["deflation_n_family_frozen_skipped_reason"] == "FAMILY_EXCLUDED_DEGENERATE"


def test_a_positive_frozen_value_passes_through_unchanged_with_no_skip_reason():
    proposal = {"symbol": "PLTR.ETORO", "strategy": "TrendPullbackStrategy"}
    study = _study({"deflation_n_family_frozen": 1839, "family_membership": None})
    record, _checks = _study_record(proposal, study, CFG)
    assert record["deflation_n_family_frozen"] == 1839
    assert record["deflation_n_family_frozen_skipped_reason"] is None


def test_missing_frozen_stamp_stays_none_without_a_spurious_skip_reason():
    """Ein Study-Record, dessen Study den Stempel nie gesetzt hat (z. B. sehr alter Report, vor
    #1091), bleibt ``None`` OHNE einen erfundenen Skip-Grund -- ``None`` bedeutet hier "nie
    gesetzt", nicht "auf 0 abgebildet"."""
    proposal = {"symbol": "TSLA.ETORO", "strategy": "TrendPullbackStrategy"}
    study = _study({})
    record, _checks = _study_record(proposal, study, CFG)
    assert record["deflation_n_family_frozen"] is None
    assert record["deflation_n_family_frozen_skipped_reason"] is None


def test_a_raw_zero_without_the_degenerate_label_still_becomes_none_with_a_generic_reason():
    """Falls ``symbol_n_family_stage1`` je einen 0-Wert OHNE die explizite #981/#1135-Markierung
    liefert (theoretisch moeglich, z. B. eine Study, die schlicht 0 Stage1-Kandidaten hat), bleibt
    das Akzeptanzkriterium 'nie 0' trotzdem gewahrt -- mit einem generischen statt dem
    degenerate-spezifischen Grund."""
    proposal = {"symbol": "GHOST.ETORO", "strategy": "GhostStrategy"}
    study = _study({"deflation_n_family_frozen": 0, "family_membership": None})
    record, _checks = _study_record(proposal, study, CFG)
    assert record["deflation_n_family_frozen"] is None
    assert record["deflation_n_family_frozen_skipped_reason"] == "FAMILY_N_ZERO"


def test_negative_frozen_value_is_also_never_exported_as_is():
    proposal = {"symbol": "BUG.ETORO", "strategy": "BugStrategy"}
    study = _study({"deflation_n_family_frozen": -1, "family_membership": None})
    record, _checks = _study_record(proposal, study, CFG)
    assert record["deflation_n_family_frozen"] is None


# --- deflation.sr0_multiple_testing_robust: fail-loud bei n_trials < 1 ------------------------------

def test_sr0_multiple_testing_robust_rejects_zero_trials_fail_loud():
    with pytest.raises(ValueError, match="n_trials"):
        deflation.sr0_multiple_testing_robust(1e-4, 0, min_cohort=10, n_periods=200)


def test_sr0_multiple_testing_robust_rejects_negative_trials_fail_loud():
    with pytest.raises(ValueError, match="n_trials"):
        deflation.sr0_multiple_testing_robust(1e-4, -3, min_cohort=10, n_periods=200)


def test_sr0_multiple_testing_robust_still_accepts_n_trials_equal_one_as_no_multiple_testing():
    """Regressionswaechter: N=1 (ein einzelner Trial, "kein Multiple-Testing") bleibt ein gültiger,
    NICHT fail-loud abgelehnter Fall -- nur N < 1 (leere/nicht existente Kohorte) ist der neue
    Fehlerfall (#1159 unterscheidet explizit zwischen beiden)."""
    sr0, *_ = deflation.sr0_multiple_testing_robust(1e-4, 1, min_cohort=10, n_periods=200)
    assert sr0 == 0.0


def test_sr0_multiple_testing_robust_still_works_for_a_realistic_family_size():
    sr0, *_ = deflation.sr0_multiple_testing_robust(1e-4, 20, min_cohort=10, n_periods=200)
    assert sr0 > 0.0
