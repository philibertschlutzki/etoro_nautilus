"""Issue #820 (P1, Katalog #817-#821, GitHub-Issue #749) — Qualitäts-Floor ohne
``R_symbol > R_global``; Cross-Snapshot-Vergleich; ``global_best``-Verdeckung.

Drei getrennte Root-Causes, gemeinsame Ursache (die Qualitätsprüfung war eindimensional):
  (a) ``champion_is_admissible`` prüfte nur ``R_symbol >= champion_min_R_symbol`` — 21 von 76
      Store-Einträgen (27,6%) hatten ``R_symbol <= R_global`` (der ungetunte globale Default war
      mindestens so gut). Fix: ``champion_min_tuning_edge``.
  (b) ``store_champion`` verglich ``R_symbol`` über Katalog-Snapshot-Grenzen hinweg roh. Fix: ein
      Snapshot-Wechsel macht den Amtsinhaber "nicht vergleichbar" — der neuere Kandidat gewinnt
      immer, ``corroboration_count`` bleibt erhalten.
  (c) ``run_optimization.load_global_best`` konnte ein reines Boolean-Flag (z. B.
      ``allow_short``) als "globales Optimum" zurückgeben und damit die Champion-Stufe für eine
      GANZE Strategie dauerhaft verdecken. Fix: Filterung auf tatsächlich tunbare Parameter.
"""
import json
from pathlib import Path

from automation.optimizer import champions, run_optimization as ro, trial_config


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
}


class _FakeStudy:
    def __init__(self, best_value=1.0):
        self.best_value = best_value
        self.directions = ["maximize"]


def _promotion(*, symbol_params, R_symbol, R_global=0.0) -> dict:
    return {
        "promote": True, "status": "READY_FOR_PR", "is_rejection_detail_override": None,
        "symbol_params": symbol_params, "R_symbol": R_symbol, "R_global": R_global,
        "promotion_margin": 0.1, "holdout_passed": True, "trial_dir": "trial_0001",
        "metrics_symbol": {}, "metrics_global": {},
    }


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(champions, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: CFG_DIR)
    monkeypatch.setattr(trial_config, "config_dir", lambda: CFG_DIR)


# ── (a) champion_min_tuning_edge ────────────────────────────────────────────────────────────────
def test_r_symbol_below_or_equal_r_global_is_not_admissible(tmp_path, monkeypatch):
    """Der schlimmste im Katalog belegte Fall: DynamicBreakout/GOOGL R_symbol=+0.329 vs.
    R_global=+1.553 -- der ungetunte Default war klar besser."""
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promo = _promotion(symbol_params={"p": 1}, R_symbol=0.329, R_global=1.553)
    path = champions.store_champion(study, "S", "GOOGL.ETORO", promo,
                                    catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    assert path is None


def test_r_symbol_strictly_above_r_global_is_admissible(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promo = _promotion(symbol_params={"p": 1}, R_symbol=1.0, R_global=0.2)
    path = champions.store_champion(study, "S", "GOOGL.ETORO", promo,
                                    catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    assert path is not None


# ── (b) Cross-Snapshot-Vergleich ────────────────────────────────────────────────────────────────
def test_cross_snapshot_replace_preserves_corroboration_and_bumps_rollover(tmp_path, monkeypatch):
    """Akzeptanzkriterium #820: store_champion mit abweichendem data_snapshot_sha256 und
    NIEDRIGEREM R_symbol ersetzt den Amtsinhaber und erhoeht snapshot_rollover_count, OHNE
    corroboration_count zurueckzusetzen."""
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    fingerprints = iter(["snap_a", "snap_b"])
    monkeypatch.setattr(champions, "catalog_fingerprint", lambda: next(fingerprints))

    promo1 = _promotion(symbol_params={"sma_period": 20}, R_symbol=0.9)
    champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo1,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")

    # ANDERE Region (weit entfernter sma_period) UND niedrigerer R_symbol, aber NEUERER Snapshot
    # -- gewinnt trotzdem, weil der Amtsinhaber auf einem anderen Snapshot nicht vergleichbar ist.
    promo2 = _promotion(symbol_params={"sma_period": 99}, R_symbol=0.3)
    path = champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo2,
                                    catalog_newest_ns=2000, opt_data=OPT_DATA, run_id="run2")
    stored = json.loads(path.read_text("utf-8"))
    assert stored["quality"]["R_symbol"] == 0.3
    assert stored["params"]["sma_period"] == 99
    assert stored["integrity"]["data_snapshot_sha256"] == "snap_b"
    assert stored["lifecycle"]["snapshot_rollover_count"] == 1
    assert stored["lifecycle"]["corroboration_count"] == 1  # NICHT zurueckgesetzt (blieb bei 1)


def test_same_snapshot_lower_r_symbol_different_region_keeps_incumbent(tmp_path, monkeypatch):
    """Gegenprobe: OHNE Snapshot-Wechsel entscheidet weiterhin schlicht R_symbol (#707/#708
    unveraendert)."""
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    monkeypatch.setattr(champions, "catalog_fingerprint", lambda: "snap_a")

    promo1 = _promotion(symbol_params={"sma_period": 20}, R_symbol=0.9)
    champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo1,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    promo2 = _promotion(symbol_params={"sma_period": 99}, R_symbol=0.3)
    path = champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo2,
                                    catalog_newest_ns=2000, opt_data=OPT_DATA, run_id="run2")
    stored = json.loads(path.read_text("utf-8"))
    assert stored["params"]["sma_period"] == 20  # unveraendert, kein Snapshot-Wechsel


# ── (c) load_global_best / resolve_symbol_shrinkage_seed ───────────────────────────────────────
def test_load_global_best_filters_non_tunable_flags(tmp_path):
    (tmp_path / "strategies.json").write_text(json.dumps({"strategies": [
        {"strategy_class": "ComboTrendVwapStrategy", "params": {"allow_short": True}}]}), "utf-8")
    result = ro.load_global_best("ComboTrendVwapStrategy", tmp_path)
    assert result == {}


def test_load_global_best_keeps_tunable_params(tmp_path):
    (tmp_path / "strategies.json").write_text(json.dumps({"strategies": [
        {"strategy_class": "ComboTrendVwapStrategy",
         "params": {"allow_short": True, "sma_period": 30}}]}), "utf-8")
    result = ro.load_global_best("ComboTrendVwapStrategy", tmp_path)
    assert result == {"sma_period": 30}


def test_resolver_reaches_champion_when_global_best_is_flag_only(tmp_path, monkeypatch):
    """Akzeptanzkriterium #820: resolve_symbol_shrinkage_seed('ComboTrendVwapStrategy', …) mit
    vorhandenem Champion liefert source == 'champion' (vor dem Fix: 'global_best', weil
    {'allow_short': True} truthy war)."""
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "strategies.json").write_text(json.dumps({"strategies": [
        {"strategy_class": "ComboTrendVwapStrategy", "params": {"allow_short": True}}]}), "utf-8")

    monkeypatch.setattr(champions, "_champions_dir", lambda: (tmp_path / "champions"))
    champion_path = tmp_path / "champions" / "champion_ComboTrendVwapStrategy_TSLA_ETORO.json"
    champion_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "strategy": "ComboTrendVwapStrategy", "symbol": "TSLA.ETORO", "tier": "all",
        "params": {"sma_period": 30}, "quality": {"R_symbol": 0.5, "R_global": 0.0, "reward": 1.0},
        "provenance": {"run_id": "run1", "holdout_reject_detail": None,
                       "status_at_store": "READY_FOR_PR", "holdout_binding_gate": None,
                       "holdout_gate_deltas": {}},
        "integrity": {"reward_semantics_version": _CURRENT_REWARD_SEMANTICS_VERSION,
                      "data_snapshot_sha256": "abc", "params_schema_version": None,
                      "catalog_newest_ns": 1000},
        "lifecycle": {"first_seen_run": "run1", "first_seen_catalog_newest_ns": 1000,
                      "corroboration_count": 1, "last_seen_run": "run1", "last_R_symbol": 0.5,
                      "degrade_streak": 0, "writeback_applied": False,
                      "snapshot_rollover_count": 0, "semantics_migrated_from": None},
    }
    champion_path.write_text(json.dumps(entry), "utf-8")

    seed, source = ro.resolve_symbol_shrinkage_seed(
        "ComboTrendVwapStrategy", tmp_path, symbol="TSLA.ETORO", opt_data=OPT_DATA)
    assert source == "champion"
    assert seed == {"sma_period": 30}
