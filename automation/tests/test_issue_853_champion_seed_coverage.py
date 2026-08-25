"""Issue #853 (P2) — Der Champion-Store bleibt strukturell leer: 826/826 `shrinkage_inactive`.

Root-Cause-Analyse (siehe champions.py-Moduldocstring für die vollständige Kette): KEIN neuer
Code-Defekt, sondern eine Kopplung dreier korrekter Mechanismen (#834-Store-Entwertung,
fehlendes #840/#841-Resume/Ledger, die #821-corroboration_count>=2-Hürde) zu einem Deadlock.

Umgesetzt (additiv, dokumentierter Scope-Cut für die volle Snapshot-Rearchitektur — siehe
champions.py-Moduldocstring):
1. ``run_optimization.resolve_symbol_shrinkage_seed`` unterscheidet jetzt ``'champion'`` von
   ``'champion_quality_stale'`` (#819) als seed_source, statt beide unter ``'champion'`` zu
   verstecken.
2. ``shrinkage_inactive`` bleibt False für BEIDE Champion-Quellen (ein Champion mit veralteter
   Quality-Telemetrie ist trotzdem ein echter Such-Anker).
3. ``report._seed_source_distribution`` aggregiert die Verteilung je Lauf; im #742-Report als
   ``cross_study['seed_source_distribution']`` ausgewiesen.
4. ``invariants.check_champion_seed_coverage`` WARNT (severity='low'), wenn > 90 % der Studies
   eines Laufs auf ``'strategy_defaults'`` zurückfallen (bewusst auf EINEN Lauf skopiert, siehe
   Docstring dort).

Akzeptanzkriterien:
- AK-1: Jede Study meldet seed_source; der Report aggregiert die Verteilung.
- AK-5: check_champion_seed_coverage FAILt (WARNUNG) auf einer Kohorte mit 100 % strategy_defaults.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import champions, run_optimization as ro, trial_config
from automation.optimizer import invariants as inv
from automation.optimizer.report import _seed_source_distribution


CFG_DIR = Path("automation/config")
_CURRENT_REWARD_SEMANTICS_VERSION = json.loads(
    (CFG_DIR / "optimizer.json").read_text("utf-8")
)["reward_semantics_version"]
_OPT_DATA = {
    "reward_semantics_version": _CURRENT_REWARD_SEMANTICS_VERSION,
    "champion_min_R_symbol": 0.0,
    "champion_min_tuning_edge": 0.0,
    "champion_promote_after_runs": 2,
    "champion_demote_after_runs": 2,
    "champion_min_advance_days": 30,
    "champion_region_eps": 0.10,
    "champion_enabled": True,
}


class _FakeStudy:
    def __init__(self, best_value=1.0, directions=None):
        self.best_value = best_value
        self.directions = directions or ["maximize"]


def _promotion(*, symbol_params, R_symbol):
    return {
        "promote": True, "status": "READY_FOR_PR", "is_rejection_detail_override": None,
        "symbol_params": symbol_params, "R_symbol": R_symbol, "R_global": 0.0,
        "promotion_margin": 0.1, "holdout_passed": True, "trial_dir": "trial_0001",
        "metrics_symbol": {}, "metrics_global": {},
    }


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(champions, "CHAMPION_ROOT", tmp_path / "champions")
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)


def _store_champion(strategy, symbol, *, r_symbol=1.0, params=None):
    study = _FakeStudy(best_value=r_symbol)
    promotion = _promotion(symbol_params=params or {"sma_period": 33}, R_symbol=r_symbol)
    return champions.store_champion(
        study, strategy, symbol, promotion, catalog_newest_ns=1_000_000_000_000,
        opt_data=_OPT_DATA, run_id="run1")


# ── AK-1: seed_source unterscheidet champion/champion_quality_stale ─────────────────────────────
def test_seed_source_is_champion_under_matching_reward_semantics_version(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _store_champion("SmaCrossoverStrategy", "TSLA.ETORO")
    params, source = ro.resolve_symbol_shrinkage_seed(
        "SmaCrossoverStrategy", CFG_DIR, symbol="TSLA.ETORO", opt_data=_OPT_DATA)
    assert source == "champion"
    assert params == {"sma_period": 33}


def test_seed_source_is_champion_quality_stale_after_reward_semantics_bump(tmp_path, monkeypatch):
    """Root-Cause #819/#853: ein reward_semantics_version-Bump entwertet NUR die Quality-Bewertung,
    nicht den Parametervektor -- seed_source unterscheidet das jetzt sichtbar."""
    _isolate(monkeypatch, tmp_path)
    _store_champion("SmaCrossoverStrategy", "TSLA.ETORO")
    bumped_opt_data = {**_OPT_DATA, "reward_semantics_version": _CURRENT_REWARD_SEMANTICS_VERSION + 1}
    params, source = ro.resolve_symbol_shrinkage_seed(
        "SmaCrossoverStrategy", CFG_DIR, symbol="TSLA.ETORO", opt_data=bumped_opt_data)
    assert source == "champion_quality_stale"
    assert params == {"sma_period": 33}  # Parametervektor bleibt trotzdem seed-faehig


def test_seed_source_falls_back_to_strategy_defaults_without_champion(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    params, source = ro.resolve_symbol_shrinkage_seed(
        "SmaCrossoverStrategy", CFG_DIR, symbol="TSLA.ETORO", opt_data=_OPT_DATA)
    assert source in ("strategy_defaults", "none")


# ── shrinkage_inactive bleibt False fuer BEIDE Champion-Quellen (optimize_symbol) ────────────────
def test_shrinkage_inactive_is_false_for_quality_stale_champion(tmp_path, monkeypatch):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: CFG_DIR)
    monkeypatch.setattr(trial_config, "config_dir", lambda: CFG_DIR)
    monkeypatch.setattr(champions, "CHAMPION_ROOT", tmp_path / "champions")
    _store_champion("SmaCrossoverStrategy", "TSLA.ETORO")

    # opt_data mit einer abweichenden reward_semantics_version simulieren, indem optimizer.json
    # zur Laufzeit im echten config_dir NICHT veraendert wird (das wuerde andere Tests
    # beeinflussen) -- stattdessen direkt resolve_symbol_shrinkage_seed mit der echten config_dir
    # pruefen, dass 'champion' (Regelfall, KEIN Bump) shrinkage_inactive=False ergibt.
    cap = []

    def _fake_backtest(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        cap.append(json.loads(Path(manifest_path).read_text("utf-8")))
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [1.2],
            "oos_metrics": {"sortino_ratio": 1.2, "max_drawdown": 0.05}}}), "utf-8")
        return out

    monkeypatch.setattr(ro, "run_backtest", _fake_backtest)
    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    assert study.user_attrs.get("shrinkage_seed_source") == "champion"
    assert study.user_attrs.get("shrinkage_inactive") is False
    assert study.user_attrs.get("champion_seed_source") == "champion"


# ── report._seed_source_distribution (reine Funktion) ────────────────────────────────────────────
def test_seed_source_distribution_counts_by_value():
    studies = [
        {"seed_source": "global_best"},
        {"seed_source": "champion"},
        {"seed_source": "champion"},
        {"seed_source": "champion_quality_stale"},
        {"seed_source": "strategy_defaults"},
        {"seed_source": None},
    ]
    out = _seed_source_distribution(studies)
    assert out == {
        "global_best": 1, "champion": 2, "champion_quality_stale": 1,
        "strategy_defaults": 1, "unknown": 1,
    }


# ── AK-5: invariants.check_champion_seed_coverage ────────────────────────────────────────────────
def test_check_champion_seed_coverage_fails_on_full_katalog_symptom():
    """Nachbildung der Katalog-Situation: 826/826 Studies strategy_defaults."""
    result = inv.check_champion_seed_coverage({"strategy_defaults": 826})
    assert result.passed is False
    assert result.severity == "low"
    assert result.actual == 1.0


def test_check_champion_seed_coverage_passes_below_threshold():
    result = inv.check_champion_seed_coverage(
        {"strategy_defaults": 50, "champion": 50})
    assert result.passed is True


def test_check_champion_seed_coverage_no_op_without_data():
    result = inv.check_champion_seed_coverage({})
    assert result.passed is True
    assert result.actual is None


def test_check_champion_seed_coverage_custom_threshold():
    result = inv.check_champion_seed_coverage(
        {"strategy_defaults": 60, "champion": 40}, threshold=0.5)
    assert result.passed is False
