"""Issue #649 — Vier `eligible_requires_all`-Gates still inaktiv (Config-Key ≠ `condition_map`-Key).

`tournament.json` listet die neueren Gates MIT `oos_`-Präfix (`oos_min_psr`,
`oos_min_excess_return`, `oos_min_profitable_folds_frac`, `oos_min_evaluable_folds`);
`_evaluate_oos_eligibility`s `condition_map` ist durchgehend un-präfigiert. Ohne Normalisierung
werden die vier Klauseln STILL übersprungen (kein Fehler, keine Warnung) — genau der Fixture-vs-
Produktion-Drift, den `test_issue_614_psr_reward_base.py` (eigene, un-präfigierte Fixture) nicht
aufdeckte.

Dieser Test lädt die ECHTE `automation/config/tournament.json` und assertet: jede
`eligible_requires_all`/`_any`-Klausel resolved (nach `_canonical_gate_key`-Normalisierung) zu einem
`condition_map`-Handler; ein absichtlich falscher Key lässt `load_tournament_config` fail-loud
abbrechen.
"""
import json
from pathlib import Path

import pytest

from automation.backtest_runner import (
    _canonical_gate_key, OOS_CONDITION_MAP_KEYS, _evaluate_oos_eligibility,
)

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def test_every_requires_all_clause_resolves_to_a_condition_map_handler():
    """Akzeptanzkriterium (#649): jede eligible_requires_all-Klausel der ECHTEN Config resolved
    (nach kanonischer Normalisierung) zu einem tatsächlichen condition_map-Handler."""
    for clause in TCFG["eligible_requires_all"]:
        canon = _canonical_gate_key(clause)
        assert canon in OOS_CONDITION_MAP_KEYS, (
            f"'{clause}' (kanonisch '{canon}') hat KEINEN condition_map-Handler — "
            f"wäre still übersprungen worden (die #649-Root-Cause)."
        )


def test_every_requires_any_clause_resolves_to_a_condition_map_handler():
    for clause in TCFG["eligible_requires_any"]:
        canon = _canonical_gate_key(clause)
        assert canon in OOS_CONDITION_MAP_KEYS


def test_the_four_previously_dead_prefixed_clauses_are_present_and_resolve():
    """Die vier im Issue benannten, vormals toten Klauseln sind in der echten Config vorhanden UND
    resolven jetzt (kanonisch) zu einem Handler."""
    dead_before_fix = [
        "oos_min_profitable_folds_frac", "oos_min_evaluable_folds",
        "oos_min_psr", "oos_min_excess_return",
    ]
    for clause in dead_before_fix:
        assert clause in TCFG["eligible_requires_all"], f"{clause} fehlt in eligible_requires_all"
        assert _canonical_gate_key(clause) in OOS_CONDITION_MAP_KEYS


def test_load_tournament_config_fails_loud_on_unknown_gate_name(tmp_path, monkeypatch):
    """Ein absichtlich falscher (nicht auf einen Handler resolvender) Key lässt
    load_tournament_config fail-loud (ValueError) abbrechen — kein stilles Überspringen."""
    from automation.backtest_runner import load_tournament_config
    from automation.optimizer import trial_config

    bad_cfg = dict(TCFG)
    bad_cfg["eligible_requires_all"] = list(TCFG["eligible_requires_all"]) + ["oos_min_totally_bogus"]
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "tournament.json").write_text(json.dumps(bad_cfg), "utf-8")

    import automation.backtest_runner as br
    monkeypatch.setattr(br, "config_dir", lambda: cfg_dir)

    with pytest.raises(ValueError, match="min_totally_bogus"):
        load_tournament_config(str(tmp_path))


def test_real_config_loads_without_raising():
    """Die tatsächlich ausgelieferte Config MUSS klaglos laden (Regressionsschutz — der fail-loud
    Registry-Check darf auf der echten Datei nicht selbst feuern)."""
    from automation.backtest_runner import load_tournament_config
    cfg = load_tournament_config()
    assert cfg["eligible_requires_all"]


# ── Invariante (#649-Akzeptanzkriterium): kein Trial mit negativem Excess-Return ODER PSR < Schwelle
# erreicht oos_eligible == True, sobald die Benchmark-/PSR-Telemetrie vorliegt. ─────────────────────
def test_negative_excess_return_trial_is_not_eligible():
    oos = {
        "total_trades": 300, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.1,
        "expectancy": 0.01, "sortino_ratio": 1.5, "psr": 0.9,
        "profit_factor": 1.3, "median_position_notional": 1000.0,
        "oos_folds_total": 4, "oos_fold_sortinos": [1.5, 1.4, 1.6, 1.5],
        "oos_excess_return": -0.01,   # unterbietet Buy&Hold ⇒ muss das Alpha-Gate verwerfen
    }
    ev = _evaluate_oos_eligibility(oos, TCFG)
    assert ev["oos_eligible"] is False
    assert any("oos_min_excess_return" in r for r in ev["oos_rejection_reasons"])


def test_low_psr_trial_is_not_eligible():
    oos = {
        "total_trades": 300, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.1,
        "expectancy": 0.01, "sortino_ratio": -1.0, "psr": 0.10,
        "profit_factor": 1.3, "median_position_notional": 1000.0,
        "oos_folds_total": 4, "oos_fold_sortinos": [-1.0, -0.9, -1.1, -1.0],
        "oos_excess_return": 0.02,
    }
    ev = _evaluate_oos_eligibility(oos, TCFG)
    assert ev["oos_eligible"] is False
    assert any("oos_min_psr" in r for r in ev["oos_rejection_reasons"])
