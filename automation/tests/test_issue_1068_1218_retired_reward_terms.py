"""Issue #1068/#1218 (P1, Katalog #1196-1221) — Drei von sechs Reward-Termen sind in 14/14 Läufen
inert.

Symptom: `check_reward_term_variance` failt in allen 14 Läufen für 13-14 von 14 Studies. Konstant
genannt: `param_pen`, `turnover`, `fold_dispersion`; bei Squeeze zusätzlich `divergence`.
`reward_std` 0,238-5,191.

Root-Cause: die drei Terme sind gegen eine frühere Base-Skala kalibriert (asinh-Sortino, vor
#614/#630) und tragen nach den Base-Wechseln weniger als 1% der Reward-Streuung.

Fix (Option "explizit deaktivieren" statt Reskalierung ohne echten Kalibrierlauf):
1. `fold_dispersion_weight`/`lambda_reg`/`penalty_turnover_weight` in optimizer.json auf 0.0.
2. `reward.py` zwingt `turnover_penalty` zusätzlich CODE-seitig auf 0.0 (der
   `round_trip_cost_bps`-Fallback-Pfad ignoriert das genullte Gewicht sonst).
3. `reward.RETIRED_REWARD_TERMS` — die neue Registry mit Begründung je Term.
4. `reward_semantics_version` 23→24.
5. `invariants.check_reward_term_variance`/`reward_term_variance_table` nehmen retirierte Terme
   von der Alarm-Liste aus (analog `_CONFIGURED_INACTIVE_REWARD_TERMS`).

`divergence` (im Issue nur für SqueezeBreakoutStrategy als zusätzlich inert genannt) bleibt AKTIV
— eine strategie-spezifische Inertheit rechtfertigt keine globale Retirement-Entscheidung.

Akzeptanzkriterien: `check_reward_term_variance` passiert oder nennt ausschliesslich Terme, die in
`reward_term_retired` (hier: `reward.RETIRED_REWARD_TERMS`) stehen. Kein Term wird berechnet,
dessen Beitrag < 1% ist.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import invariants as inv
from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import RETIRED_REWARD_TERMS, compute_reward

CFG = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))


# --- reward.RETIRED_REWARD_TERMS: die neue Registry --------------------------------------------

def test_retired_terms_are_exactly_the_three_universally_inert_terms():
    assert set(RETIRED_REWARD_TERMS) == {"param_pen", "turnover", "fold_dispersion"}


def test_divergence_is_not_retired_despite_squeeze_specific_inertness():
    """Das Issue nennt divergence nur fuer SqueezeBreakoutStrategy zusaetzlich inert -- eine
    strategie-spezifische Beobachtung rechtfertigt keine globale Retirement-Entscheidung."""
    assert "divergence" not in RETIRED_REWARD_TERMS


def test_every_retired_term_carries_a_non_empty_reason():
    for term, reason in RETIRED_REWARD_TERMS.items():
        assert isinstance(reason, str) and len(reason) > 20, term


# --- optimizer.json: Gewichte genullt, Version gebumpt ------------------------------------------

def test_the_three_weights_are_zeroed_in_the_shipped_config():
    assert CFG["fold_dispersion_weight"] == 0.0
    assert CFG["lambda_reg"] == 0.0
    assert CFG["penalty_turnover_weight"] == 0.0


def test_reward_semantics_version_bumped_to_24():
    # Issue #1078/#1226 bumped reward_semantics_version further (24 -> 25); wie jeder andere
    # Bump-Test dieser Familie (test_issue_637/658/672/686/697/711/766/781/815/834/854/912/936/961
    # -- allesamt ">="), verifiziert dieser Test nur, dass der #1068-Bump auf 24 monoton ERHALTEN
    # blieb, nicht, dass 24 die aktuelle Version ist.
    assert CFG["reward_semantics_version"] >= 24


def test_version_history_documents_the_1218_bump():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    assert "v24" in doc
    for ref in ("#1068", "#1218"):
        assert ref in doc, f"v24-Changelog muss {ref} referenzieren"


# --- reward.compute_reward: die drei Terme sind IMMER 0.0, auch bei explizit gesetzten Gewichten -

def _m(**overrides):
    base = dict(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=1.5,
        oos_sortino=1.5, oos_max_drawdown=0.02, oos_total_trades=200, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=0.05,
        oos_fold_returns=[0.05, -0.05, 0.05, -0.05], oos_folds_total=4,
        round_trip_cost_bps=16.0,
    )
    base.update(overrides)
    return TournamentMetrics(**base)


_NONZERO_WEIGHTS = {
    "penalty_unevaluable_oos": -4.0, "sortino_clip_abs": 3.0,
    "penalty_overfit_weight": 0.5, "penalty_dd_weight": 0.0, "bonus_coverage_weight": 1.0,
    "unevaluable_shaping_span": 0.25, "oos_min_trades": 20, "penalty_scale_vs_base": 1.0,
    # Absichtlich NICHT genullt -- verifiziert den CODE-seitigen Zwang unabhaengig vom Gewicht.
    "penalty_turnover_weight": 0.0003, "fold_dispersion_weight": 0.5, "lambda_reg": 0.25,
}


def test_turnover_is_zero_even_with_a_nonzero_weight_and_available_cost_telemetry():
    """Der kritische Fall aus der Root-Cause-Analyse: round_trip_cost_bps ist verfuegbar (der
    Pfad, den penalty_turnover_weight=0.0 NICHT deaktiviert) -- turnover_penalty muss trotzdem
    0.0 sein."""
    _, terms = compute_reward(
        _m(), universe_size=1, weights=_NONZERO_WEIGHTS, holdout=True, return_terms=True)
    assert terms["turnover"] == 0.0


def test_param_pen_is_zero_even_with_a_nonzero_lambda_reg():
    _, terms = compute_reward(
        _m(), universe_size=1, weights=_NONZERO_WEIGHTS, holdout=True,
        return_terms=True, sampled={"x": 5.0}, global_params={"x": 1.0}, strategy="SqueezeBreakoutStrategy")
    assert terms["param_pen"] == 0.0


def test_fold_dispersion_is_zero_even_with_a_nonzero_weight():
    _, terms = compute_reward(
        _m(), universe_size=1, weights=_NONZERO_WEIGHTS, holdout=False, return_terms=True)
    assert terms["fold_dispersion"] == 0.0


# --- invariants.check_reward_term_variance: retirierte Terme werden nicht mehr alarmiert --------

def _trial(reward_terms):
    return {"oos_evaluated": True, "reward_terms": reward_terms}


def test_check_reward_term_variance_excludes_all_three_retired_terms():
    """Reproduziert den #1218-Befund: alle drei Terme sind konstant NULL (die neue Realitaet nach
    der Retirierung) -- der Check darf sie NICHT mehr als Anomalie melden."""
    trials = [
        _trial({"base": b, "divergence": 0.1 * i, "dd_penalty": 0.0, "param_pen": 0.0,
                "turnover": 0.0, "fold_dispersion": 0.0, "tie_breaker": 0.0})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    result = inv.check_reward_term_variance(trials)
    assert result.passed is True
    assert result.actual == []


def test_check_reward_term_variance_excludes_retired_terms_even_with_residual_pre_v24_variance():
    """Uebergangs-Kohorte (Mischung aus Pre-v24-Trials mit nicht-null Werten und Post-v24-Trials
    mit exakt 0.0): die retirierten Terme werden weiterhin NICHT alarmiert, unabhaengig von ihrer
    Streuung -- das Akzeptanzkriterium ("nennt ausschliesslich Terme, die in reward_term_retired
    stehen") ist mit dieser Ausnahme unbedingt erfuellt."""
    trials = [
        _trial({"base": b, "divergence": 0.1 * i, "dd_penalty": 0.0,
                "param_pen": 0.001 * i, "turnover": 0.002 * i, "fold_dispersion": 0.003 * i,
                "tie_breaker": 0.0})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    result = inv.check_reward_term_variance(trials)
    assert "param_pen" not in result.actual
    assert "turnover" not in result.actual
    assert "fold_dispersion" not in result.actual


def test_check_reward_term_variance_still_catches_a_genuinely_new_inert_term():
    """Die Ausnahme ist NICHT ein Blankoscheck: ein anderer, NICHT retirierter Term (hier:
    divergence), der konstant bleibt, waehrend base variiert, wird weiterhin gemeldet."""
    trials = [
        _trial({"base": b, "divergence": 0.0, "dd_penalty": 0.0, "param_pen": 0.0,
                "turnover": 0.0, "fold_dispersion": 0.0, "tie_breaker": 0.0,
                "gate_distance_penalty": 0.5 * i})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    # gate_distance_penalty variiert (kein Alarm), divergence ist konstant 0.0 -> per "all zero"
    # bereits uebersprungen (kein aktiver, aber undokumentiert inerter Term in diesem Fixture) --
    # dieser Test verifiziert nur, dass RETIRED_REWARD_TERMS die generelle Inertheits-Erkennung
    # NICHT abschaltet, indem er einen dritten, NICHT-null-aber-inerten Term konstruiert.
    trials2 = [
        _trial({"base": b, "divergence": 0.0001 * (i % 2), "dd_penalty": 0.0, "param_pen": 0.0,
                "turnover": 0.0, "fold_dispersion": 0.0, "tie_breaker": 0.0})
        for i, b in enumerate([1.0, 5.0, 10.0, 0.5, 20.0])
    ]
    result = inv.check_reward_term_variance(trials2)
    assert "divergence" in result.actual
    assert result.passed is False


# --- invariants.reward_term_variance_table: configured_inactive erfasst retirierte Terme --------

def test_variance_table_marks_retired_terms_as_configured_inactive():
    trials = [
        _trial({"base": b, "divergence": 0.1 * i, "dd_penalty": 0.0, "param_pen": 0.0,
                "turnover": 0.0, "fold_dispersion": 0.0, "tie_breaker": 0.0})
        for i, b in enumerate([1.0, 1.5, 2.0, 0.5, 3.0])
    ]
    table = inv.reward_term_variance_table(trials)
    by_term = {row["term"]: row for row in table}
    for term in ("param_pen", "turnover", "fold_dispersion"):
        assert by_term[term]["configured_inactive"] is True
