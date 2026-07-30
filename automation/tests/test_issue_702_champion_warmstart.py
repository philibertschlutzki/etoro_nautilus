"""Epic #702 (Issues #703-#710) — Iterativer Champion-Warm-Start & symbol-skopierte
Default-Nachführung. Gehärtet durch den Issue-Katalog #817-#821 (GitHub-Issue #749) — siehe die
jeweiligen ``test_issue_817_*``/``test_issue_818_*``/``test_issue_819_*``/``test_issue_820_*``/
``test_issue_821_*``-Dateien für die Regressionstests DIESER Härtung; dieses Modul deckt weiterhin
den ursprünglichen #702-Kern ab und wird nur dort angepasst, wo sich das GRUNDVERHALTEN geändert
hat (REJECT_HOLDOUT_GATE ist nicht mehr pauschal zulässig, ein reward_semantics_version-Mismatch
ist nicht mehr hart inadmissibel, ``store_champion`` verlangt jetzt ``run_id``).

Drei Ebenen (siehe GitHub-Issue #705 §3 + ``automation/optimizer/champions.py``-Docstring):
  Ebene 1 (Such-Anker, jeder Lauf): ``champions.store_champion`` / ``load_champion_seed`` /
    ``load_champion_entry`` (#703/#704), zentrale Guards in ``champion_is_admissible`` (#705).
  Ebene 2 (Default-Nachführung, korroboriert): ``champions.maybe_write_back`` (#706), Regions-
    Korroboration/Lifecycle (#707).
  Ebene 3 (Live) bleibt unverändert menschlich (HI-3) — kein Test hier schreibt je
    ``strategies.json``.

Mirrors die etablierten Test-Konventionen aus ``test_issue_565_shrinkage_fallback.py``
(Resolver-Präzedenz, HI-7-Mocking), ``test_per_symbol_promotion.py`` (Promotion-Dict-Form) und
``test_issue_652_family_wide_multiplicity.py`` (Study-/Sweep-Isolation).
"""
import json
import statistics
from pathlib import Path

import pytest

from automation.optimizer import champions, run_optimization as ro, resolve, trial_config


CFG_DIR = Path("automation/config")
# Issue #711 — dynamisch aus der AKTUELLEN optimizer.json gelesen (statt hartkodiert), damit
# dieses Fixture jeden künftigen reward_semantics_version-Bump automatisch mitgeht (dasselbe
# Muster wie test_issue_697_gate_consolidation.py). Tests, die GEZIELT eine VERALTETE Version
# prüfen wollen, setzen sie explizit (z. B. {**OPT_DATA, "reward_semantics_version": 12}).
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


# ── Fixtures / Helpers ───────────────────────────────────────────────────────────────────────
class _FakeStudy:
    def __init__(self, best_value=1.0, directions=None):
        self.best_value = best_value
        self.directions = directions or ["maximize"]


def _promotion(*, status, symbol_params, R_symbol, R_global=0.0,
              is_rejection_detail_override=None, trial_dir="trial_0001",
              metrics_symbol=None) -> dict:
    return {
        "promote": status == "READY_FOR_PR",
        "status": status,
        "is_rejection_detail_override": is_rejection_detail_override,
        "symbol_params": symbol_params,
        "R_symbol": R_symbol,
        "R_global": R_global,
        "promotion_margin": 0.1,
        "holdout_passed": status not in ("REJECTED_ON_HOLDOUT",),
        "trial_dir": trial_dir,
        "metrics_symbol": metrics_symbol or {},
        "metrics_global": {},
    }


def _entry(*, params=None, r_symbol=0.5, r_global=0.0,
          reward_version=_CURRENT_REWARD_SEMANTICS_VERSION,
          status_at_store="READY_FOR_PR",
          holdout_reject_detail=None, holdout_binding_gate=None, holdout_gate_deltas=None,
          degrade_streak=0, corroboration_count=1, last_seen_run="20260101T000000Z",
          catalog_newest_ns=1_000_000_000_000, first_seen_catalog_newest_ns=1_000_000_000_000,
          strategy="SmaCrossoverStrategy", symbol="TSLA.ETORO") -> dict:
    return {
        "strategy": strategy, "symbol": symbol, "tier": "all",
        "params": params if params is not None else {"sma_period": 20},
        "quality": {"R_symbol": r_symbol, "R_global": r_global, "reward": 1.0},
        "provenance": {"run_id": "20260101T000000Z", "source_trial_dir": "x",
                       "holdout_reject_detail": holdout_reject_detail,
                       "status_at_store": status_at_store,
                       "holdout_binding_gate": holdout_binding_gate,
                       "holdout_gate_deltas": holdout_gate_deltas or {}},
        "integrity": {"reward_semantics_version": reward_version, "data_snapshot_sha256": "abc",
                      "params_schema_version": None, "catalog_newest_ns": catalog_newest_ns},
        "lifecycle": {"first_seen_run": "20260101T000000Z",
                      "first_seen_catalog_newest_ns": first_seen_catalog_newest_ns,
                      "corroboration_count": corroboration_count, "last_R_symbol": r_symbol,
                      "last_seen_run": last_seen_run,
                      "degrade_streak": degrade_streak, "writeback_applied": False,
                      "snapshot_rollover_count": 0, "semantics_migrated_from": None},
    }


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(champions, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: CFG_DIR)
    monkeypatch.setattr(trial_config, "config_dir", lambda: CFG_DIR)


def _fake_factory(sortino=1.2, dd=0.05):
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino],
            "oos_metrics": {"sortino_ratio": sortino, "max_drawdown": dd}}}), "utf-8")
        return out
    return _fake


# ── champion_is_admissible (#705) — die zentrale Guard-Einheit, rein/pure ──────────────────────
def test_admissible_happy_path():
    ok, reason = champions.champion_is_admissible(_entry(), OPT_DATA)
    assert ok is True and reason is None


def test_inadmissible_empty_params():
    ok, reason = champions.champion_is_admissible(_entry(params={}), OPT_DATA)
    assert ok is False and reason == "EMPTY_PARAMS"


def test_inadmissible_no_viable_trial_status():
    ok, reason = champions.champion_is_admissible(
        _entry(status_at_store="NO_VIABLE_TRIAL"), OPT_DATA)
    assert ok is False and reason == "INADMISSIBLE_STATUS"


@pytest.mark.parametrize("detail", ["HOLDOUT_NO_ELIGIBLE_TRIALS", "REJECT_HOLDOUT_UNREACHABLE"])
def test_inadmissible_holdout_unreached(detail):
    """Issue #703 Punkt 2 — der Kandidat hat den Holdout nie erreicht -> nichts zu übernehmen."""
    ok, reason = champions.champion_is_admissible(
        _entry(status_at_store="REJECTED_ON_HOLDOUT", holdout_reject_detail=detail), OPT_DATA)
    assert ok is False and reason == "HOLDOUT_UNREACHED"


@pytest.mark.parametrize("detail", ["REJECT_SELECTION_PBO", "REJECT_BOUNDARY_SOLUTION"])
def test_inadmissible_rejection_not_allowlisted(detail):
    """Issue #703 Punkt 2 — Overfit-geflaggte (PBO) bzw. Randlösungs-Kandidaten (Boundary)
    destabilisieren die Folgesuche und sind explizit von der Seeding-Allowlist ausgeschlossen."""
    ok, reason = champions.champion_is_admissible(
        _entry(status_at_store="REJECTED_SELECTION_OVERFIT", holdout_reject_detail=detail), OPT_DATA)
    assert ok is False and reason == "REJECTION_NOT_ALLOWLISTED"


@pytest.mark.parametrize("detail", ["REJECT_HOLDOUT_DSR_DROP", "REJECT_HOLDOUT_BOOTSTRAP_CI"])
def test_admissible_rejection_allowlist(detail):
    """Diese zwei Rejection-Details tragen einen validen, evaluierten Parametervektor, der den
    Holdout-Gate SELBST bestanden hat (nur an der Multiplizitäts-/Konfidenzkorrektur gescheitert)
    — sie bleiben seed-würdig trotz gescheiterter Promotion. ``REJECT_HOLDOUT_GATE`` ist SEIT
    #817 NICHT mehr Teil dieser einfachen Allowlist (siehe test_issue_817_holdout_gate_allowlist.py
    für dessen eigenes, strengeres Zulassungskriterium)."""
    ok, _ = champions.champion_is_admissible(
        _entry(status_at_store="REJECTED_ON_HOLDOUT", holdout_reject_detail=detail), OPT_DATA)
    assert ok is True


def test_inadmissible_below_quality_floor():
    ok, reason = champions.champion_is_admissible(_entry(r_symbol=-0.1), OPT_DATA)
    assert ok is False and reason == "BELOW_QUALITY_FLOOR"


def test_inadmissible_r_symbol_none():
    entry = _entry()
    entry["quality"]["R_symbol"] = None
    ok, reason = champions.champion_is_admissible(entry, OPT_DATA)
    assert ok is False and reason == "BELOW_QUALITY_FLOOR"


def test_reward_semantics_mismatch_is_quality_stale_not_inadmissible():
    """Issue #819 — ein reiner reward_semantics_version-Mismatch schliesst NICHT mehr aus (der
    Parametervektor bleibt seed-fähig, siehe champion_quality_stale für die Writeback-Sperre)."""
    entry = _entry(reward_version=12)
    ok, reason = champions.champion_is_admissible(entry, OPT_DATA)
    assert ok is True and reason is None
    assert champions.champion_quality_stale(entry, OPT_DATA) is True
    assert champions.champion_quality_stale(_entry(), OPT_DATA) is False


def test_inadmissible_reward_semantics_missing_in_opt_data():
    ok, reason = champions.champion_is_admissible(_entry(), {**OPT_DATA, "reward_semantics_version": None})
    assert ok is False and reason == "REWARD_SEMANTICS_MISMATCH"


def test_inadmissible_demoted():
    ok, reason = champions.champion_is_admissible(_entry(degrade_streak=2), OPT_DATA)
    assert ok is False and reason == "DEMOTED"


def test_admissible_below_demote_threshold():
    ok, _ = champions.champion_is_admissible(_entry(degrade_streak=1), OPT_DATA)
    assert ok is True


def test_champion_disabled_kill_switch():
    ok, reason = champions.champion_is_admissible(_entry(), {**OPT_DATA, "champion_enabled": False})
    assert ok is False and reason == "CHAMPION_DISABLED"


# ── Issue #820 — relatives Qualitätskriterium (Tuning-Edge) ────────────────────────────────────
def test_inadmissible_no_tuning_edge_when_not_better_than_global():
    ok, reason = champions.champion_is_admissible(
        _entry(r_symbol=0.5, r_global=0.6), OPT_DATA)
    assert ok is False and reason == "NO_TUNING_EDGE"


def test_admissible_when_r_symbol_exceeds_global_by_margin():
    ok, _ = champions.champion_is_admissible(
        _entry(r_symbol=0.5, r_global=0.6), {**OPT_DATA, "champion_min_tuning_edge": -0.2})
    assert ok is True


def test_admissible_with_no_global_baseline():
    """Fehlt R_global (nie OOS-evaluiert), gilt die Edge-Bedingung trivial als erfüllt."""
    entry = _entry(r_symbol=0.1)
    entry["quality"]["R_global"] = None
    ok, _ = champions.champion_is_admissible(entry, OPT_DATA)
    assert ok is True


# ── store_champion (#703) — Persistenz + Merge-Logik ────────────────────────────────────────────
def test_store_champion_persists_admissible_candidate(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy(best_value=2.5)
    promotion = _promotion(status="REJECTED_ON_HOLDOUT", symbol_params={"sma_period": 33},
                           R_symbol=0.4, is_rejection_detail_override="REJECT_HOLDOUT_DSR_DROP")
    path = champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                                    catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    assert path is not None and path.exists()
    stored = json.loads(path.read_text("utf-8"))
    assert stored["params"] == {"sma_period": 33}
    assert stored["quality"]["R_symbol"] == 0.4
    assert stored["provenance"]["status_at_store"] == "REJECTED_ON_HOLDOUT"
    assert stored["lifecycle"]["corroboration_count"] == 1
    assert stored["lifecycle"]["last_seen_run"] == "run1"


def test_store_champion_requires_run_id(tmp_path, monkeypatch):
    """Issue #821 — run_id ist ein Pflichtparameter; kein automatisch geminteter Zeitstempel mehr."""
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promotion = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 33}, R_symbol=1.0)
    with pytest.raises(ValueError):
        champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                                 catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="")


def test_store_champion_never_touches_strategies_json(tmp_path, monkeypatch):
    """HI-3 — der Champion-Store betrifft ausschliesslich data/optimizer/champions/*.json."""
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promotion = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 33}, R_symbol=1.0)
    champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    assert not (tmp_path / "strategies.json").exists()
    assert (tmp_path / "champions" / "champion_SmaCrossoverStrategy_TSLA_ETORO.json").exists()


def test_store_champion_returns_none_for_zero_override_keys(tmp_path, monkeypatch):
    """Issue #703 — override-keys==0 (HOLDOUT_NO_ELIGIBLE_TRIALS) -> nichts zu übernehmen."""
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promotion = _promotion(status="REJECTED_ON_HOLDOUT", symbol_params={}, R_symbol=0.0,
                           is_rejection_detail_override="HOLDOUT_NO_ELIGIBLE_TRIALS")
    path = champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                                    catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    assert path is None
    assert not (tmp_path / "champions").exists() or not list((tmp_path / "champions").glob("*.json"))


def test_store_champion_returns_none_below_quality_floor(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promotion = _promotion(status="REJECTED_ON_HOLDOUT", symbol_params={"sma_period": 33},
                           R_symbol=-0.4, is_rejection_detail_override="REJECT_HOLDOUT_DSR_DROP")
    path = champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                                    catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    assert path is None


@pytest.mark.parametrize("detail", ["REJECT_SELECTION_PBO", "REJECT_BOUNDARY_SOLUTION"])
def test_store_champion_returns_none_for_disallowed_rejection(tmp_path, monkeypatch, detail):
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promotion = _promotion(status="REJECTED_SELECTION_OVERFIT", symbol_params={"sma_period": 33},
                           R_symbol=0.4, is_rejection_detail_override=detail)
    path = champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                                    catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    assert path is None


def test_store_champion_disabled_via_kill_switch(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promotion = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 33}, R_symbol=1.0)
    path = champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promotion,
                                    catalog_newest_ns=1000, opt_data={**OPT_DATA, "champion_enabled": False},
                                    run_id="run1")
    assert path is None


def test_store_champion_reward_version_mismatch_preserves_history(tmp_path, monkeypatch):
    """Issue #819 — ein reward_semantics_version-Mismatch verwirft den Eintrag NICHT mehr
    vollständig: params/corroboration_count überleben den Bump, nur quality wird neu gesetzt."""
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    # Ersteintrag unter v12 speichern (opt_data mit alter Version).
    old_opt = {**OPT_DATA, "reward_semantics_version": 12}
    promo_old = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 10}, R_symbol=0.9)
    champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo_old,
                             catalog_newest_ns=1000, opt_data=old_opt, run_id="run1")
    # Neuer Kandidat unter der AKTUELLEN Version, ANDERER Lauf -> quality_stale-Merge.
    promo_new = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 44}, R_symbol=0.5)
    path = champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo_new,
                                    catalog_newest_ns=2000, opt_data=OPT_DATA, run_id="run2")
    stored = json.loads(path.read_text("utf-8"))
    assert stored["params"] == {"sma_period": 44}  # der frisch bewertete Kandidat gewinnt
    assert stored["lifecycle"]["corroboration_count"] == 2  # Historie erhalten, run-gegated erhöht
    assert stored["lifecycle"]["semantics_migrated_from"] == 12
    assert stored["integrity"]["reward_semantics_version"] == _CURRENT_REWARD_SEMANTICS_VERSION


def test_store_champion_corroborates_same_region(tmp_path, monkeypatch):
    """Issue #707 — region-gleiche Kandidaten AUS VERSCHIEDENEN LÄUFEN erhöhen corroboration_count
    und übernehmen den besseren R_symbol-Vektor (Issue #821 — run_id-gegated, s. eigener Test
    unten für den Negativfall "gleiche run_id")."""
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promo1 = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 20, "cooldown_bars": 5},
                        R_symbol=0.5)
    champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo1,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    # minimal abweichender, aber region-gleicher (kleine Distanz) UND besserer Kandidat.
    promo2 = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 21, "cooldown_bars": 5},
                        R_symbol=0.8)
    path = champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo2,
                                    catalog_newest_ns=2000, opt_data=OPT_DATA, run_id="run2")
    stored = json.loads(path.read_text("utf-8"))
    assert stored["lifecycle"]["corroboration_count"] == 2
    assert stored["params"]["sma_period"] == 21  # besserer R_symbol übernommen
    assert stored["lifecycle"]["first_seen_catalog_newest_ns"] == 1000  # Erstsichtung erhalten


def test_store_champion_same_run_id_does_not_double_count(tmp_path, monkeypatch):
    """Issue #821 — zwei store_champion-Aufrufe MIT DERSELBEN run_id (z. B. ein Confirm-Retry
    innerhalb desselben Sweeps) erhöhen corroboration_count NICHT."""
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promo1 = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 20, "cooldown_bars": 5},
                        R_symbol=0.5)
    champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo1,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    promo2 = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 21, "cooldown_bars": 5},
                        R_symbol=0.8)
    path = champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo2,
                                    catalog_newest_ns=2000, opt_data=OPT_DATA, run_id="run1")
    stored = json.loads(path.read_text("utf-8"))
    assert stored["lifecycle"]["corroboration_count"] == 1


def test_store_champion_different_region_worse_keeps_existing(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promo1 = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 20}, R_symbol=0.9)
    champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo1,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    # Weit entfernte Region, schlechterer R_symbol -> darf den etablierten Champion NICHT verdrängen.
    promo2 = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 99}, R_symbol=0.1)
    path = champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo2,
                                    catalog_newest_ns=2000, opt_data=OPT_DATA, run_id="run2")
    stored = json.loads(path.read_text("utf-8"))
    assert stored["params"]["sma_period"] == 20
    assert stored["lifecycle"]["corroboration_count"] == 1


def test_store_champion_different_region_better_replaces(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promo1 = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 20}, R_symbol=0.2)
    champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo1,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    promo2 = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 99}, R_symbol=0.9)
    path = champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo2,
                                    catalog_newest_ns=2000, opt_data=OPT_DATA, run_id="run2")
    stored = json.loads(path.read_text("utf-8"))
    assert stored["params"]["sma_period"] == 99
    assert stored["lifecycle"]["corroboration_count"] == 1  # frischer Lifecycle


# ── Demotion (#708) ──────────────────────────────────────────────────────────────────────────
def test_store_champion_demotes_after_repeated_degradation(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    strategy, symbol = "SmaCrossoverStrategy", "TSLA.ETORO"
    promo_good = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 20}, R_symbol=0.9)
    champions.store_champion(study, strategy, symbol, promo_good,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")

    champion_path = tmp_path / "champions" / f"champion_{strategy}_TSLA_ETORO.json"
    assert champion_path.exists()

    # Zwei aufeinanderfolgende region-gleiche, aber unter-dem-Floor-liegende Re-Evaluierungen
    # (champion_demote_after_runs=2) -> Demotion.
    degraded_low = _promotion(status="REJECTED_ON_HOLDOUT", symbol_params={"sma_period": 21},
                              R_symbol=-0.2, is_rejection_detail_override="REJECT_HOLDOUT_GATE")
    champions.store_champion(study, strategy, symbol, degraded_low,
                             catalog_newest_ns=2000, opt_data=OPT_DATA, run_id="run2")
    assert champion_path.exists()  # degrade_streak=1, noch nicht demotet
    stored_after_1 = json.loads(champion_path.read_text("utf-8"))
    assert stored_after_1["lifecycle"]["degrade_streak"] == 1

    champions.store_champion(study, strategy, symbol, degraded_low,
                             catalog_newest_ns=3000, opt_data=OPT_DATA, run_id="run3")
    assert not champion_path.exists()  # degrade_streak=2 >= champion_demote_after_runs -> demotet


def test_store_champion_degrade_streak_resets_on_healthy_reevaluation(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    strategy, symbol = "SmaCrossoverStrategy", "TSLA.ETORO"
    promo_good = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 20}, R_symbol=0.9)
    champions.store_champion(study, strategy, symbol, promo_good,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    degraded_low = _promotion(status="REJECTED_ON_HOLDOUT", symbol_params={"sma_period": 21},
                              R_symbol=-0.2, is_rejection_detail_override="REJECT_HOLDOUT_GATE")
    champions.store_champion(study, strategy, symbol, degraded_low,
                             catalog_newest_ns=2000, opt_data=OPT_DATA, run_id="run2")
    # eine erneute, region-gleiche Evaluierung WIEDER über dem Floor -> degrade_streak resettet.
    healthy_again = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 22}, R_symbol=0.7)
    path = champions.store_champion(study, strategy, symbol, healthy_again,
                                    catalog_newest_ns=3000, opt_data=OPT_DATA, run_id="run3")
    stored = json.loads(path.read_text("utf-8"))
    assert stored["lifecycle"]["degrade_streak"] == 0


# ── load_champion_seed / load_champion_entry (#704) ─────────────────────────────────────────────
def test_load_champion_seed_reads_admissible_entry(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promo = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 33}, R_symbol=0.9)
    champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    seed = champions.load_champion_seed("SmaCrossoverStrategy", "TSLA.ETORO", CFG_DIR,
                                        opt_data=OPT_DATA)
    assert seed == {"sma_period": 33}


def test_load_champion_seed_empty_for_missing_entry(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    seed = champions.load_champion_seed("SmaCrossoverStrategy", "TSLA.ETORO", CFG_DIR,
                                        opt_data=OPT_DATA)
    assert seed == {}


def test_load_champion_seed_survives_stale_reward_version(tmp_path, monkeypatch):
    """Issue #819 — ein reward_semantics_version-Mismatch entwertet nur die QUALITY-Bewertung; der
    Parametervektor bleibt als Seed lesbar (er durchläuft beim Enqueue ohnehin erneut ALLE Gates
    auf frischen Daten)."""
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promo = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 33}, R_symbol=0.9)
    champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    stale_opt = {**OPT_DATA, "reward_semantics_version": 999}  # Study/Config inzwischen weitergebumpt
    seed = champions.load_champion_seed("SmaCrossoverStrategy", "TSLA.ETORO", CFG_DIR,
                                        opt_data=stale_opt)
    assert seed == {"sma_period": 33}


def test_load_champion_seed_empty_when_config_missing_reward_version(tmp_path, monkeypatch):
    """Eine degenerierte Config (reward_semantics_version fehlt ganz) bleibt inadmissibel."""
    _isolate(monkeypatch, tmp_path)
    study = _FakeStudy()
    promo = _promotion(status="READY_FOR_PR", symbol_params={"sma_period": 33}, R_symbol=0.9)
    champions.store_champion(study, "SmaCrossoverStrategy", "TSLA.ETORO", promo,
                             catalog_newest_ns=1000, opt_data=OPT_DATA, run_id="run1")
    broken_opt = {**OPT_DATA, "reward_semantics_version": None}
    seed = champions.load_champion_seed("SmaCrossoverStrategy", "TSLA.ETORO", CFG_DIR,
                                        opt_data=broken_opt)
    assert seed == {}


# ── resolve_symbol_shrinkage_seed / optimize_symbol Integration (#704) — Scenario #1 ────────────
def test_resolver_uses_champion_between_global_best_and_defaults(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(champions, "_champions_dir", lambda: (tmp_path / "champions"))
    champion_path = tmp_path / "champions" / "champion_SmaCrossoverStrategy_TSLA_ETORO.json"
    champion_path.parent.mkdir(parents=True, exist_ok=True)
    champion_path.write_text(json.dumps(_entry(params={"sma_period": 77}, r_symbol=0.6)), "utf-8")

    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {})  # kein READY_FOR_PR-Proposal
    seed, source = ro.resolve_symbol_shrinkage_seed(
        "SmaCrossoverStrategy", CFG_DIR, symbol="TSLA.ETORO", opt_data=OPT_DATA)
    assert source == "champion"
    assert seed == {"sma_period": 77}


def test_resolver_prefers_global_best_over_champion(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(champions, "_champions_dir", lambda: (tmp_path / "champions"))
    champion_path = tmp_path / "champions" / "champion_SmaCrossoverStrategy_TSLA_ETORO.json"
    champion_path.parent.mkdir(parents=True, exist_ok=True)
    champion_path.write_text(json.dumps(_entry(params={"sma_period": 77}, r_symbol=0.6)), "utf-8")

    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {"sma_period": 5})
    seed, source = ro.resolve_symbol_shrinkage_seed(
        "SmaCrossoverStrategy", CFG_DIR, symbol="TSLA.ETORO", opt_data=OPT_DATA)
    assert source == "global_best"
    assert seed == {"sma_period": 5}


def test_resolver_without_symbol_or_opt_data_is_legacy_bit_identical(monkeypatch):
    """HI-2 — fehlen symbol/opt_data (Legacy-Aufrufer), ist das Verhalten bit-identisch zum
    Pre-#704-Zustand: keine Champion-Stufe."""
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {})
    seed, source = ro.resolve_symbol_shrinkage_seed("SmaCrossoverStrategy", CFG_DIR)
    assert source == "strategy_defaults"
    assert seed.get("sma_period") == 5


def test_optimize_symbol_enqueues_champion_seed_when_no_global_best(tmp_path, monkeypatch):
    """Scenario #1 (Issue #705 §8) — kein READY_FOR_PR-Proposal, aber ein zulässiger Champion mit
    R_symbol>=0 -> resolve_symbol_shrinkage_seed liefert source=='champion', der erste Trial trägt
    die Champion-Params, shrinkage_inactive bleibt False (echter Anker)."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _fake_factory())
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {})

    study0 = _FakeStudy()
    promo = _promotion(status="REJECTED_ON_HOLDOUT", symbol_params={"sma_period": 41},
                       R_symbol=0.3, is_rejection_detail_override="REJECT_HOLDOUT_DSR_DROP")
    champions.store_champion(study0, "SmaCrossoverStrategy", "TSLA.ETORO", promo,
                             catalog_newest_ns=1000,
                             opt_data=OPT_DATA, run_id="run1")

    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=1)
    assert study.user_attrs.get("shrinkage_seed_source") == "champion"
    assert study.user_attrs.get("shrinkage_inactive") is False
    assert study.trials[0].params.get("sma_period") == 41
    assert study.user_attrs.get("champion_R_symbol_at_store") == 0.3
    assert study.user_attrs.get("champion_corroboration_count") == 1


# ── maybe_write_back (#706) — Scenario #6 (Writeback-Gate) + #7 (Symbol-Scope) ───────────────────
# Jeder Test isoliert ``champions.WORK`` (== ``_champions_dir()``'s Basis): ``maybe_write_back``
# schreibt den Champion-Store-Eintrag am Ende IMMER zurück (``_write_entry(_champion_path(...))``),
# unabhängig vom ``base_cfg``-Parameter (der nur ``strategy_symbol_seeds.json`` betrifft) — ohne
# Isolation würde jeder dieser Tests das REALE ``data/optimizer/champions/`` verschmutzen.
def test_writeback_ready_for_pr_is_immediate(tmp_path, monkeypatch):
    monkeypatch.setattr(champions, "WORK", tmp_path)
    seeds_cfg = tmp_path
    entry = _entry(status_at_store="READY_FOR_PR", corroboration_count=1)
    ok = champions.maybe_write_back(entry, OPT_DATA, base_cfg=seeds_cfg)
    assert ok is True
    assert entry["lifecycle"]["writeback_applied"] is True
    written = json.loads((seeds_cfg / "strategy_symbol_seeds.json").read_text("utf-8"))
    assert written["seeds"]["SmaCrossoverStrategy"]["TSLA.ETORO"] == {"sma_period": 20}


def test_writeback_requires_corroboration_below_threshold_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(champions, "WORK", tmp_path)
    entry = _entry(status_at_store="REJECTED_ON_HOLDOUT", holdout_reject_detail="REJECT_HOLDOUT_GATE",
                   corroboration_count=1)  # champion_promote_after_runs=2
    ok = champions.maybe_write_back(entry, OPT_DATA, base_cfg=tmp_path)
    assert ok is False
    assert not (tmp_path / "strategy_symbol_seeds.json").exists()


def test_writeback_corroborated_but_identical_window_fails_snooping_guard(tmp_path, monkeypatch):
    """Scenario #6 — auf IDENTISCHEM Datenfenster (kein Fensterfortschritt) darf trotz erreichter
    Korroboration KEIN Writeback erfolgen (Snooping-Schutz)."""
    monkeypatch.setattr(champions, "WORK", tmp_path)
    entry = _entry(status_at_store="REJECTED_ON_HOLDOUT", holdout_reject_detail="REJECT_HOLDOUT_GATE",
                   corroboration_count=2, catalog_newest_ns=1_000_000_000_000,
                   first_seen_catalog_newest_ns=1_000_000_000_000)  # advance_days == 0
    ok = champions.maybe_write_back(entry, OPT_DATA, base_cfg=tmp_path)
    assert ok is False
    assert not (tmp_path / "strategy_symbol_seeds.json").exists()


def test_writeback_corroborated_and_window_advanced_succeeds(tmp_path, monkeypatch):
    """champion_min_advance_days=30 -> 40 Tage Fortschritt genügt."""
    monkeypatch.setattr(champions, "WORK", tmp_path)
    forty_days_ns = 40 * 86400 * 1_000_000_000
    entry = _entry(status_at_store="REJECTED_ON_HOLDOUT", holdout_reject_detail="REJECT_HOLDOUT_GATE",
                   corroboration_count=2, catalog_newest_ns=1_000_000_000_000 + forty_days_ns,
                   first_seen_catalog_newest_ns=1_000_000_000_000)
    ok = champions.maybe_write_back(entry, OPT_DATA, base_cfg=tmp_path)
    assert ok is True


def test_writeback_never_touches_strategy_defaults_json(tmp_path, monkeypatch):
    """Scenario #7 (Symbol-Scope) — landet AUSSCHLIESSLICH in strategy_symbol_seeds.json[strategy]
    [symbol], NIE in strategy_defaults.json."""
    monkeypatch.setattr(champions, "WORK", tmp_path)
    (tmp_path / "strategy_defaults.json").write_text(
        json.dumps({"SmaCrossoverStrategy": {"sma_period": 5}}), "utf-8")
    entry = _entry(status_at_store="READY_FOR_PR", corroboration_count=1)
    champions.maybe_write_back(entry, OPT_DATA, base_cfg=tmp_path)
    defaults_after = json.loads((tmp_path / "strategy_defaults.json").read_text("utf-8"))
    assert defaults_after == {"SmaCrossoverStrategy": {"sma_period": 5}}  # unverändert
    seeds_after = json.loads((tmp_path / "strategy_symbol_seeds.json").read_text("utf-8"))
    assert seeds_after["seeds"]["SmaCrossoverStrategy"]["TSLA.ETORO"] == {"sma_period": 20}


def test_writeback_disabled_via_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(champions, "WORK", tmp_path)
    entry = _entry(status_at_store="READY_FOR_PR")
    ok = champions.maybe_write_back(entry, {**OPT_DATA, "champion_enabled": False}, base_cfg=tmp_path)
    assert ok is False


def test_writeback_reward_version_mismatch_fails(tmp_path, monkeypatch):
    """Issue #819 — ein quality_stale Eintrag bleibt SEED-fähig, aber NICHT writeback-fähig."""
    monkeypatch.setattr(champions, "WORK", tmp_path)
    entry = _entry(status_at_store="READY_FOR_PR", reward_version=1)
    ok = champions.maybe_write_back(entry, OPT_DATA, base_cfg=tmp_path)
    assert ok is False


# ── resolve.resolve_params: strategy_symbol_seeds.json Präzedenz (#706) ─────────────────────────
def test_resolve_params_applies_symbol_seed_between_defaults_and_strategies(tmp_path):
    (tmp_path / "strategy_defaults.json").write_text(
        json.dumps({"VwapExhaustionStrategy": {"vwap_period": 24, "cooldown_bars": 3}}), "utf-8")
    (tmp_path / "strategy_symbol_seeds.json").write_text(json.dumps({
        "seeds": {"VwapExhaustionStrategy": {"TSLA.ETORO": {"vwap_period": 40}}}}), "utf-8")
    (tmp_path / "strategies.json").write_text(json.dumps({"strategies": []}), "utf-8")
    out = resolve.resolve_params("VwapExhaustionStrategy", {}, tmp_path, instrument="TSLA.ETORO")
    assert out["vwap_period"] == 40          # seed beats strategy_defaults
    assert out["cooldown_bars"] == 3         # untouched key still from defaults


def test_resolve_params_strategies_json_overrides_symbol_seed(tmp_path):
    """strategies.json[params] bleibt oberhalb des Symbol-Seeds (ein menschlicher PR gewinnt
    immer gegen einen automatisch nachgeführten Seed)."""
    (tmp_path / "strategy_defaults.json").write_text(
        json.dumps({"VwapExhaustionStrategy": {"vwap_period": 24}}), "utf-8")
    (tmp_path / "strategy_symbol_seeds.json").write_text(json.dumps({
        "seeds": {"VwapExhaustionStrategy": {"TSLA.ETORO": {"vwap_period": 40}}}}), "utf-8")
    (tmp_path / "strategies.json").write_text(json.dumps({"strategies": [
        {"strategy_class": "VwapExhaustionStrategy", "params": {"vwap_period": 55}}]}), "utf-8")
    out = resolve.resolve_params("VwapExhaustionStrategy", {}, tmp_path, instrument="TSLA.ETORO")
    assert out["vwap_period"] == 55


def test_resolve_params_symbol_seed_inactive_without_instrument(tmp_path):
    """HI-2 — ohne instrument bleibt das Verhalten bit-identisch zum Pre-#706-Zustand."""
    (tmp_path / "strategy_defaults.json").write_text(
        json.dumps({"VwapExhaustionStrategy": {"vwap_period": 24}}), "utf-8")
    (tmp_path / "strategy_symbol_seeds.json").write_text(json.dumps({
        "seeds": {"VwapExhaustionStrategy": {"TSLA.ETORO": {"vwap_period": 40}}}}), "utf-8")
    out = resolve.resolve_params("VwapExhaustionStrategy", {}, tmp_path)
    assert out["vwap_period"] == 24


def test_resolve_params_no_seeds_file_is_legacy(tmp_path):
    (tmp_path / "strategy_defaults.json").write_text(
        json.dumps({"X": {"a": 1}}), "utf-8")
    out = resolve.resolve_params("X", {}, tmp_path, instrument="S")
    assert out == {"a": 1}


# ── #694-Kopplung (Issue #705 §9) — Sanity: Near-Duplicate-Champion-Reenqueues declustern ────────
def test_repeated_champion_reenqueue_declusters_to_effective_one():
    """Ohne #694/#695 (cpcv.cluster_effective_configs) würde jeder Warm-Start-Lauf denselben
    Champion um sich herum re-enqueuen und die rohe familienweite N monoton wachsen lassen. Diese
    Sanity prüft, dass die bereits verdrahtete Declustering-Maschinerie near-identische,
    champion-artige Return-Serien (wie sie ein wiederholt re-enqueueter Seed erzeugt) tatsächlich
    auf ~1 effektive Config kollabiert — die Vorbedingung, unter der Ebene 1 überhaupt aktiviert
    werden darf (Issue #705 §9: 'Reihenfolge daher: #694 vor der Aktivierung von Ebene 1')."""
    from automation.optimizer.cpcv import cluster_effective_configs
    import numpy as np

    rng = np.random.default_rng(7)
    base = rng.normal(0.001, 0.01, size=200)
    # 10 "Läufe", die denselben Champion re-enqueuen -> near-identische Return-Serien (TPE variiert
    # nur mikroskopisch um den Seed herum).
    near_duplicates = [base + rng.normal(0, 1e-6, size=200) for _ in range(10)]
    keep = cluster_effective_configs(near_duplicates, threshold=0.99)
    assert len(keep) == 1, "wiederholtes Champion-Reenqueue muss auf 1 effektive Config kollabieren"
