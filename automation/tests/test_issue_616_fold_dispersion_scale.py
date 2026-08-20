"""Issue #616 — Fold-Dispersions-Strafe war ein struktureller Blindgänger (Skalenfehler wie Pre-#597-dd).

``pstdev(fold_returns)`` (0.001–0.05) gegen ``base`` (PSR/asinh-Sortino) — ohne Normierung war der Term
Median 0.036, Maximum 0.100, drei Grössenordnungen zu klein. Fix (historisch): ``fold_dispersion_scale``
(analog ``dd_reward_scale``) normierte ihn in den Bereich der übrigen Terme.

Issue #1068/#1218 (Katalog #1196-1221, supersedes #616) — die Normierung selbst war KORREKT, aber
der Term trug auf der seit #614/#630 geltenden psr_z-Base trotzdem < 1% der Reward-Streuung in
14/14 Referenz-Läufen — ``fold_dispersion`` ist seither RETIRIERT (code-seitig auf 0.0 gezwungen,
``reward.RETIRED_REWARD_TERMS``, ``reward_semantics_version`` 24), UNABHÄNGIG vom übergebenen
``weights``-Dict. Die Tests unten pinnen die neue Realität; die historische Skalierungslogik bleibt
nur noch forensisch in ``reward.py`` (der Code-Pfad ist unerreichbar, siehe dortiger Kommentar).
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.reward import compute_reward
from automation.optimizer.parsing import TournamentMetrics

_W = {
    "penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 0.001, "evaluable_reward_floor": -12.0,
    "sortino_clip_abs": 5.0, "sortino_soft_scale": 5.0,
    "penalty_overfit_weight": 0.5, "penalty_dd_weight": 1.0, "bonus_coverage_weight": 1.0,
    "fold_dispersion_weight": 0.5, "fold_dispersion_scale": 0.03,
    "missing_fold_penalty_scale": 0.05, "w_ret": 0.0, "penalty_turnover_weight": 0.0,
}
_CFG = {"oos_min_total_return": 0.005, "max_drawdown": 0.3}


def _m(fold_returns):
    # gleicher Gesamt-Return, gleiche Base — nur die Fold-STREUUNG unterscheidet sich.
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=1.0, is_sortino_pooled=1.0,
        oos_sortino=1.5, oos_max_drawdown=0.0, oos_total_trades=40, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=0.0,
        oos_fold_returns=tuple(fold_returns), oos_folds_total=len(fold_returns))


def _terms(fr):
    _, t = compute_reward(_m(fr), universe_size=1, weights=_W, risk_dd_cap=0.3,
                          tournament_cfg=_CFG, return_terms=True)
    return t


# ── Issue #1068/#1218: fold_dispersion ist retiriert, unabhaengig vom Fold-Muster ────────────────
def test_alternating_and_consistent_folds_are_no_longer_differentiated():
    """Vormals (Akzeptanzkriterium #616): alternierende Folds strikt haerter bestraft als
    konsistente. Seit #1068/#1218 ist fold_dispersion IMMER 0.0 -- beide Faelle sind ununterscheidbar."""
    inconsistent = _terms([0.05, -0.05, 0.05, -0.05])   # 100 % Vorzeichenwechsel, Gesamt 0
    consistent = _terms([0.01, 0.01, 0.01, 0.01])        # pstdev 0
    assert inconsistent["fold_dispersion"] == 0.0
    assert consistent["fold_dispersion"] == 0.0


def test_penalty_is_zero_even_for_a_realistic_dispersion_fold():
    """Vormals: ein realistischer Dispersions-Fold (±0.03) lieferte eine Strafe im Bereich
    [0.1, 1.0]. Seit #1068/#1218 ist der Term code-seitig auf 0.0 gezwungen."""
    t = _terms([0.03, -0.03, 0.03, -0.03])   # pstdev 0.03 ⇒ vormals norm 1.0 ⇒ penalty 0.5
    assert t["fold_dispersion"] == 0.0


def test_scale_absent_still_yields_zero_penalty():
    """Fehlt fold_dispersion_scale (Legacy-Roh-Skala-Pfad) -- auch dieser Pfad ist seit #1068/#1218
    unerreichbar, der Term bleibt 0.0."""
    w = dict(_W)
    w.pop("fold_dispersion_scale")
    _, t = compute_reward(_m([0.05, -0.05, 0.05, -0.05]), universe_size=1, weights=w,
                          risk_dd_cap=0.3, tournament_cfg=_CFG, return_terms=True)
    assert t["fold_dispersion"] == 0.0


def test_fold_dispersion_penalty_is_zero_even_with_an_explicit_nonzero_weight():
    """Der CODE-seitige Zwang (reward.py) gilt UNABHAENGIG vom uebergebenen weights-Dict -- selbst
    ein explizit gesetztes fold_dispersion_weight != 0 (wie hier in _W) reaktiviert den Term nicht."""
    assert _W["fold_dispersion_weight"] != 0.0  # das Test-Fixture setzt bewusst einen alten Wert
    t = _terms([0.05, -0.05, 0.05, -0.05])
    assert t["fold_dispersion"] == 0.0


def test_shipped_config_has_fold_dispersion_weight_zeroed_since_1218():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["fold_dispersion_weight"] == 0.0
    assert "fold_dispersion_scale" in cfg["_schema"]["fields"]
