"""Issue #1094/#1242 (P1, Fix Punkt 3) — ``--corroboration-pass``-Betriebsart: re-evaluiert
gespeicherte Champions gegen das aktuelle Datenfenster, ohne eine neue Optuna-Suche.

Root-Cause. Korroboration (``champions.store_champion``s ``_bump_corroboration``, #821) setzt
voraus, dass dasselbe (Strategie, Symbol)-Paar erneut GESUCHT wird (ein zweiter
``store_champion``-Aufruf mit anderer ``run_id``). Unter einer ``least_recently_covered``-
Abdeckungsrotation, die bewusst Breite statt Wiederholung maximiert, ist das strukturell selten
(``max(corroboration_count)=1`` in 7/11 Läufen im Katalog).

Fix. ``sweep.run_corroboration_pass`` iteriert AUSSCHLIESSLICH bereits gespeicherte Champions,
re-evaluiert jeden Parametervektor gegen das aktuelle Fenster über den leichtgewichtigen
Symbol-Holdout-Gate-Check (``confirm._holdout_metrics_for_params``/``_holdout_gate_passed`` —
denselben, den ``confirm_on_holdout`` für den globalen Baseline-Vektor nutzt) und ruft bei Erfolg
denselben ``store_champion``/``_attempt_champion_writeback``-Pfad auf wie ein regulärer Sweep-
Kandidat. Berührt NIEMALS ``confirm.confirm_per_symbol_promotion`` (die DSR-/Familien-
Multiplizitätsstufe) — Akzeptanzkriterium 3: ``deflation_n_family_raw`` bleibt unverändert.
"""
import json
import logging
from pathlib import Path
from types import SimpleNamespace

from automation.optimizer import champions, confirm, sweep, trial_config


CFG_DIR = Path("automation/config")
_CURRENT_REWARD_SEMANTICS_VERSION = json.loads(
    (CFG_DIR / "optimizer.json").read_text("utf-8")
)["reward_semantics_version"]
OPT_DATA = {
    "reward_semantics_version": _CURRENT_REWARD_SEMANTICS_VERSION,
    "champion_min_R_symbol": 0.0,
    "champion_min_tuning_edge": 0.0,
    "champion_promote_after_runs": 2,
    "champion_demote_after_runs": 2,
    "champion_min_advance_days": 30,
    "champion_region_eps": 0.10,
    "champion_enabled": True,
    "champion_corroboration_mode": "independent_search",
}


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(sweep, "WORK", tmp_path)
    monkeypatch.setattr(champions, "CHAMPION_ROOT", tmp_path / "champions")
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "config_dir", lambda: tmp_path)


class _FakeStudy:
    best_value = 1.0
    directions = ["maximize"]


def _promotion(**overrides):
    base = {
        "promote": True, "status": "READY_FOR_PR", "is_rejection_detail_override": None,
        "symbol_params": {"sma_period": 33}, "R_symbol": 0.9, "R_global": 0.0,
        "promotion_margin": 0.1, "holdout_passed": True, "trial_dir": "trial_0001",
        "metrics_symbol": {}, "metrics_global": {},
    }
    base.update(overrides)
    return base


def _fake_passing_metrics(total_return=0.05):
    return SimpleNamespace(
        oos_evaluated=True, oos_eligible=True, oos_max_drawdown=0.05,
        oos_sortino=1.0, oos_total_return=total_return, holdout_trial_dir="trial_pass",
    )


def _fake_failing_metrics():
    return SimpleNamespace(
        oos_evaluated=True, oos_eligible=True, oos_max_drawdown=0.05,
        oos_sortino=-0.5, oos_total_return=-0.02, holdout_trial_dir="trial_fail",
    )


def test_corroboration_pass_reconfirms_and_bumps_corroboration_without_new_search(
        tmp_path, monkeypatch):
    """Kern-Akzeptanzkriterium (#1242): ein gespeicherter Champion mit corroboration_count=1 wird
    über EINEN corroboration-pass (keine Optuna-Suche, kein optimize_symbol) auf 2 gehoben und der
    Writeback greift — genau der Pfad, den die Rotation strukturell selten erreicht."""
    _isolate(monkeypatch, tmp_path)
    champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", _promotion(),
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    stored_before = json.loads(
        (tmp_path / "champions" / "champion_SmaCrossoverStrategy_TSLA_ETORO.json").read_text("utf-8"))
    assert stored_before["lifecycle"]["corroboration_count"] == 1

    monkeypatch.setattr(confirm, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **k: _fake_passing_metrics())
    monkeypatch.setattr(sweep, "latest_ts_by_symbol", lambda symbols: {s: 2000 for s in symbols})

    # Akzeptanzkriterium 3 — dieser Pfad darf die DSR-/Familien-Multiplizitaetsstufe NIE
    # beruehren; ein Aufruf waere ein struktureller Fehler in dieser Betriebsart.
    def _must_not_be_called(*a, **k):
        raise AssertionError("run_corroboration_pass darf confirm_per_symbol_promotion nie aufrufen")
    monkeypatch.setattr(confirm, "confirm_per_symbol_promotion", _must_not_be_called)

    summary = sweep.run_corroboration_pass(opt_data=OPT_DATA, run_id="corroboration_pass_run2")

    assert summary["attempted"] == 1
    assert summary["reconfirmed"] == 1
    assert summary["writeback_attempts"] == 1
    assert summary["skipped_no_entry"] == 0

    stored_after = json.loads(
        (tmp_path / "champions" / "champion_SmaCrossoverStrategy_TSLA_ETORO.json").read_text("utf-8"))
    assert stored_after["lifecycle"]["corroboration_count"] == 2
    assert stored_after["lifecycle"]["writeback_applied"] is True

    seeds = json.loads((tmp_path / "strategy_symbol_seeds.json").read_text("utf-8"))
    assert seeds["seeds"]["SmaCrossoverStrategy"]["TSLA.ETORO"] == {"sma_period": 33}


def test_corroboration_pass_does_not_bump_when_gate_fails(tmp_path, monkeypatch):
    """Ein Champion, dessen Parametervektor das Holdout-Gate auf dem aktuellen Fenster NICHT mehr
    besteht, wird nicht reklassifiziert und der Store bleibt unveraendert."""
    _isolate(monkeypatch, tmp_path)
    champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", _promotion(),
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    monkeypatch.setattr(confirm, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **k: _fake_failing_metrics())
    monkeypatch.setattr(sweep, "latest_ts_by_symbol", lambda symbols: {s: 2000 for s in symbols})

    summary = sweep.run_corroboration_pass(opt_data=OPT_DATA, run_id="corroboration_pass_run2")

    assert summary["attempted"] == 1
    assert summary["reconfirmed"] == 0
    assert summary["writeback_attempts"] == 0

    stored_after = json.loads(
        (tmp_path / "champions" / "champion_SmaCrossoverStrategy_TSLA_ETORO.json").read_text("utf-8"))
    assert stored_after["lifecycle"]["corroboration_count"] == 1


def test_corroboration_pass_no_stored_champions_is_a_no_op(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    summary = sweep.run_corroboration_pass(opt_data=OPT_DATA)
    assert summary == {"attempted": 0, "reconfirmed": 0, "writeback_attempts": 0,
                       "skipped_no_entry": 0, "results": []}


def test_corroboration_pass_respects_strategy_and_symbol_filters(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "TSLA.ETORO", _promotion(),
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    champions.store_champion(_FakeStudy(), "SmaCrossoverStrategy", "NVDA.ETORO",
                             _promotion(symbol_params={"sma_period": 20}),
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    monkeypatch.setattr(confirm, "_holdout_metrics_for_params",
                        lambda strategy, symbol, params, **k: _fake_passing_metrics())
    monkeypatch.setattr(sweep, "latest_ts_by_symbol", lambda symbols: {s: 2000 for s in symbols})

    summary = sweep.run_corroboration_pass(
        opt_data=OPT_DATA, symbols=["TSLA.ETORO"], run_id="corroboration_pass_run2")
    assert summary["attempted"] == 1
    assert [r["symbol"] for r in summary["results"]] == ["TSLA.ETORO"]
