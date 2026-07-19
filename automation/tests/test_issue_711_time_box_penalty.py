"""Issue #711 (P1) — Time-Box-Penalty additiv in `compute_reward` (Req-04, chirurgisch).

#708-Req-04 fordert `Obj = E[R]/σ_R⁻ − β·(t_hold/24)²`. Wörtlich umgesetzt ersetzt das die
getestete `psr_z`-Base durch rohen Sortino — ein Rückschritt hinter #559–#702 (v7–v13). Fix: NUR
der additive Term wird in die bestehende Assembly aufgenommen, direkt neben dd_penalty/
turnover_penalty. `base = psr_z`, Gates, Deflation, Confirm bleiben unberührt.

Akzeptanzkriterien:
- β=0 ⇒ bit-identische Rewards (Skalen-Fingerprint test_issue_637 UND Eligibility-Fingerprint
  unverändert).
- β>0 ⇒ Reward sinkt monoton in oos_median_bars_held bei sonst identischen Metriken.
- assert_penalty_scale_calibrated deckt den neuen Term ab; return_terms enthält time_box_penalty.
- reward_semantics_version 13→14 (compute_reward berührt) + Pflicht-Purge, letzte Aktion vor Re-Run.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward, assert_penalty_scale_calibrated

OPT_CFG_PATH = Path("automation/config/optimizer.json")
OPT_CFG = json.loads(OPT_CFG_PATH.read_text("utf-8"))

_TCFG = {
    "oos_min_trades": 10, "oos_min_total_return": 0.0, "oos_min_expectancy": 0.0,
    "oos_min_win_rate": 0.0, "max_drawdown": 0.3,
}


def _make_metrics(bars_held: float | None, **overrides) -> TournamentMetrics:
    kwargs = dict(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=0.0,
        oos_sortino=1.0, oos_max_drawdown=0.01, oos_total_trades=50, win_count=1,
        fully_eligible_pairs=1, is_total_trades=50, oos_total_return=0.02,
        oos_win_rate=0.4, oos_profit_factor=1.3, oos_psr_z=0.8,
        oos_fold_returns=(0.01, 0.012, 0.009, 0.011), oos_folds_total=4,
        oos_median_bars_held=bars_held,
    )
    kwargs.update(overrides)
    return TournamentMetrics(**kwargs)


# ── β=0 ⇒ bit-identisch ──────────────────────────────────────────────────────────────────────
def test_beta_zero_is_bit_identical_to_term_absent():
    weights_without = dict(OPT_CFG)
    weights_without.pop("penalty_time_box_weight", None)
    weights_with_zero = dict(OPT_CFG)
    weights_with_zero["penalty_time_box_weight"] = 0.0

    m = _make_metrics(bars_held=20.0)
    r1 = compute_reward(m, universe_size=1, weights=weights_without, risk_dd_cap=0.3, tournament_cfg=_TCFG)
    r2 = compute_reward(m, universe_size=1, weights=weights_with_zero, risk_dd_cap=0.3, tournament_cfg=_TCFG)
    assert r1 == r2


def test_default_config_has_beta_zero():
    """Der Default in optimizer.json MUSS 0.0 sein (bit-identisch/Legacy)."""
    assert OPT_CFG["penalty_time_box_weight"] == 0.0


# ── β>0 ⇒ monoton fallend in oos_median_bars_held ────────────────────────────────────────────
def test_reward_monotonically_decreasing_in_bars_held():
    weights = dict(OPT_CFG)
    weights["penalty_time_box_weight"] = 0.05

    bars_sequence = [0.0, 2.0, 6.0, 12.0, 18.0, 24.0, 30.0]
    rewards = []
    for bars in bars_sequence:
        m = _make_metrics(bars_held=bars)
        r = compute_reward(m, universe_size=1, weights=weights, risk_dd_cap=0.3, tournament_cfg=_TCFG)
        rewards.append(r)

    for earlier, later in zip(rewards, rewards[1:]):
        assert later <= earlier, f"reward muss monoton fallen: {rewards}"
    # Echte Diskriminierung (keine Plateau-Sättigung über die gesamte Spanne).
    assert rewards[0] > rewards[-1]


def test_none_bars_held_yields_zero_penalty():
    """Fehlt m.oos_median_bars_held (Legacy-JSON/Fixture ohne #710-Feld) ⇒ 0.0, kein erfundener Wert."""
    weights = dict(OPT_CFG)
    weights["penalty_time_box_weight"] = 0.05
    m_none = _make_metrics(bars_held=None)
    m_zero = _make_metrics(bars_held=0.0)
    _, terms_none = compute_reward(
        m_none, universe_size=1, weights=weights, risk_dd_cap=0.3, tournament_cfg=_TCFG, return_terms=True
    )
    assert terms_none["time_box_penalty"] == 0.0
    _, terms_zero = compute_reward(
        m_zero, universe_size=1, weights=weights, risk_dd_cap=0.3, tournament_cfg=_TCFG, return_terms=True
    )
    assert terms_zero["time_box_penalty"] == 0.0


# ── Isolation: reines Shaping, kein Gate ──────────────────────────────────────────────────────
def test_time_box_penalty_does_not_affect_eligibility_only_reward_value():
    """Ein evaluated-aber-nicht-eligible Trial UND ein eligible Trial mit identischen Metriken
    erhalten denselben time_box_penalty-Betrag — der Term unterscheidet nicht nach oos_eligible
    (Isolation von den Gates, Lektion #629/#649)."""
    weights = dict(OPT_CFG)
    weights["penalty_time_box_weight"] = 0.05
    m_eligible = _make_metrics(bars_held=15.0, oos_eligible=True)
    m_ineligible = _make_metrics(bars_held=15.0, oos_eligible=False, oos_total_trades=2)
    _, terms_e = compute_reward(
        m_eligible, universe_size=1, weights=weights, risk_dd_cap=0.3, tournament_cfg=_TCFG, return_terms=True
    )
    _, terms_i = compute_reward(
        m_ineligible, universe_size=1, weights=weights, risk_dd_cap=0.3, tournament_cfg=_TCFG, return_terms=True
    )
    assert terms_e["time_box_penalty"] == pytest.approx(terms_i["time_box_penalty"])


# ── return_terms enthält time_box_penalty (beide Reward-Pfade) ──────────────────────────────
def test_return_terms_contains_time_box_penalty_per_symbol_path():
    m = _make_metrics(bars_held=10.0)
    _, terms = compute_reward(
        m, universe_size=1, weights=dict(OPT_CFG), risk_dd_cap=0.3, tournament_cfg=_TCFG, return_terms=True
    )
    assert "time_box_penalty" in terms


def test_return_terms_contains_time_box_penalty_coverage_path():
    m = _make_metrics(bars_held=10.0)
    _, terms = compute_reward(
        m, universe_size=5, weights=dict(OPT_CFG), risk_dd_cap=0.3, tournament_cfg=_TCFG, return_terms=True
    )
    assert "time_box_penalty" in terms


# ── assert_penalty_scale_calibrated deckt den neuen Term ab ─────────────────────────────────
def test_calibration_guard_passes_with_default_config():
    assert_penalty_scale_calibrated(OPT_CFG)  # darf NICHT raisen


def test_calibration_term_keys_include_time_box_penalty():
    from automation.optimizer.reward import _CALIBRATION_PENALTY_TERM_KEYS

    assert "time_box_penalty" in _CALIBRATION_PENALTY_TERM_KEYS


def test_time_box_penalty_inactive_by_default_does_not_affect_calibration_median():
    """Default penalty_time_box_weight=0.0 ⇒ der Term ist für die Kalibrier-Kohorte strukturell
    INAKTIV (sigma=0) und wird — analog #534 (_constraint_distance_penalty: nur AKTIVE Terme
    gehen in den Mittelwert ein) — aus dem Median ausgeklammert. Der Default-Kalibrier-Check
    bleibt dadurch bit-identisch zum Pre-#711-Verhalten (median über dd_penalty/turnover/
    fold_dispersion, exakt wie vor #711)."""
    assert_penalty_scale_calibrated(OPT_CFG)  # darf NICHT raisen (identisch zu Pre-#711)


def test_calibration_guard_catches_time_box_miscalibration_combined_with_classic_631_defect():
    """Der Median-von-N-Mechanismus ist bewusst robust gegen EINEN einzelnen dominanten
    Ausreisser (nur aktive Terme, siehe oben) — ein unkalibriertes penalty_time_box_weight ALLEIN
    kann die Prüfung daher nicht im Alleingang auslösen, exakt wie es für dd_penalty/turnover/
    fold_dispersion individuell auch gilt. Aktiviert man time_box_penalty ABER zusammen mit dem
    klassischen #631-Defekt (keine Rekalibrierung, penalty_scale_vs_base=1.0), trägt der neue Term
    sichtbar zur (weiterhin korrekt erkannten) Verletzung bei — er versteckt den strukturellen
    Fehler nicht."""
    weights_uncalibrated = dict(OPT_CFG)
    weights_uncalibrated["penalty_time_box_weight"] = 0.5
    weights_uncalibrated["penalty_scale_vs_base"] = 1.0  # klassischer #631-Defekt: keine Rekalibrierung
    with pytest.raises(ValueError, match="PENALTY_SCALE_MISCALIBRATED"):
        assert_penalty_scale_calibrated(weights_uncalibrated)


# ── reward_semantics_version 13 → 14 ──────────────────────────────────────────────────────────
def test_reward_semantics_version_is_14():
    assert OPT_CFG["reward_semantics_version"] == 14


def test_version_is_documented_with_v14_changelog_entry():
    doc = OPT_CFG["_schema"]["fields"]["reward_semantics_version"]
    assert "v14" in doc
    assert "#711" in doc


def test_stale_pre_v14_study_is_rejected_fail_loud():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 13}
            self.trials = [object()] * 5
            self.study_name = "study_stale_v13"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    with pytest.raises(ValueError, match="REJECT_STALE_STUDY_SEMANTICS"):
        _check_reward_semantics_version(_FakeStudy(), OPT_CFG)


def test_fresh_study_stamps_v14_without_error():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {}
            self.trials = []
            self.study_name = "study_fresh"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    study = _FakeStudy()
    _check_reward_semantics_version(study, OPT_CFG)
    assert study.user_attrs["reward_semantics_version"] == 14
